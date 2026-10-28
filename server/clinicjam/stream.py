"""Local audio streaming: librespot PCM -> MP3 -> HTTP for the Nest group.

librespot (the Spotify Connect endpoint) writes raw 44.1 kHz s16le stereo
PCM into a FIFO. We read it non-blocking at a real-time pace, padding with
silence whenever Spotify is paused or between tracks, and feed the result
to ffmpeg for MP3 encoding. The encoded stream is fanned out to HTTP
subscribers at /stream.mp3 — the Chromecast speaker group is the one
subscriber that matters, and the continuous silence keeps it from
dropping the session during quiet moments.

Server utilization: ffmpeg only runs while somebody is actually listening
(a subscriber is connected) plus a short grace period. The FIFO is always
drained so librespot never blocks, but idle nights cost no encoder CPU.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time

log = logging.getLogger(__name__)

SAMPLE_RATE = 44100
CHANNELS = 2
BYTES_PER_SAMPLE = 2
CHUNK_SECONDS = 0.05
CHUNK_BYTES = int(SAMPLE_RATE * CHANNELS * BYTES_PER_SAMPLE * CHUNK_SECONDS)
IDLE_GRACE_SECONDS = 30.0


class StreamFull(Exception):
    pass


class Broadcaster:
    """Fan a byte stream out to a bounded number of HTTP subscribers."""

    def __init__(self, max_queue: int = 256, max_clients: int = 6):
        self.subscribers: set[asyncio.Queue[bytes]] = set()
        self.max_queue = max_queue
        self.max_clients = max_clients
        self.last_subscriber_at = 0.0

    def publish(self, chunk: bytes) -> None:
        for q in list(self.subscribers):
            if q.qsize() >= self.max_queue:
                # Slow reader: drop the oldest chunk instead of ballooning.
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()
            q.put_nowait(chunk)

    async def subscribe(self):
        if len(self.subscribers) >= self.max_clients:
            raise StreamFull()
        q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=self.max_queue + 8)
        self.subscribers.add(q)
        self.last_subscriber_at = time.monotonic()
        try:
            while True:
                yield await q.get()
        finally:
            self.subscribers.discard(q)
            self.last_subscriber_at = time.monotonic()


class AudioStreamer:
    def __init__(self, fifo_path: str, bitrate: str, max_clients: int = 6):
        self.fifo_path = fifo_path
        self.bitrate = bitrate
        self.broadcaster = Broadcaster(max_clients=max_clients)
        self._task: asyncio.Task | None = None
        self._ffmpeg: asyncio.subprocess.Process | None = None
        self.encoder_running = False

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="audio-streamer")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._kill_ffmpeg()

    def _wanted(self) -> bool:
        b = self.broadcaster
        if b.subscribers:
            return True
        return time.monotonic() - b.last_subscriber_at < IDLE_GRACE_SECONDS

    async def _kill_ffmpeg(self) -> None:
        proc, self._ffmpeg = self._ffmpeg, None
        self.encoder_running = False
        if proc and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), 5)

    async def _run(self) -> None:
        while True:
            try:
                await self._pump_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("audio pipeline crashed; restarting in 3s")
            await asyncio.sleep(3)

    async def _spawn_ffmpeg(self) -> None:
        self._ffmpeg = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-hide_banner", "-loglevel", "error", "-nostdin",
            "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS),
            "-i", "pipe:0",
            "-f", "mp3", "-b:a", self.bitrate,
            "-flush_packets", "1",
            "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self.encoder_running = True
        log.info("mp3 encoder started (%s)", self.bitrate)

    async def _pump_once(self) -> None:
        try:
            await self._feed_pcm()
        finally:
            await self._kill_ffmpeg()

    async def _read_encoded(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stdout
        while True:
            chunk = await proc.stdout.read(4096)
            if not chunk:
                return
            self.broadcaster.publish(chunk)

    def _open_fifo(self) -> int | None:
        try:
            # O_NONBLOCK so opening doesn't wait for librespot, and reads
            # return EAGAIN (-> silence) when Spotify isn't producing audio.
            return os.open(self.fifo_path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as e:
            log.warning("cannot open fifo %s: %s", self.fifo_path, e)
            return None

    async def _feed_pcm(self) -> None:
        fd = self._open_fifo()
        silence = b"\x00" * CHUNK_BYTES
        next_deadline = time.monotonic()
        reader: asyncio.Task | None = None
        fifo_retry_at = 0.0
        try:
            while True:
                next_deadline += CHUNK_SECONDS
                delay = next_deadline - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
                else:
                    # We fell behind (CPU stall); resynchronize the clock.
                    next_deadline = time.monotonic()

                data = b""
                if fd is None and time.monotonic() > fifo_retry_at:
                    fd = self._open_fifo()
                    fifo_retry_at = time.monotonic() + 5.0
                if fd is not None:
                    try:
                        data = os.read(fd, CHUNK_BYTES)
                    except BlockingIOError:
                        data = b""
                    except OSError:
                        os.close(fd)
                        fd = None

                # Start/stop the encoder based on demand.
                wanted = self._wanted()
                if wanted and self._ffmpeg is None:
                    await self._spawn_ffmpeg()
                    reader = asyncio.create_task(
                        self._read_encoded(self._ffmpeg), name="mp3-reader"
                    )
                elif not wanted and self._ffmpeg is not None:
                    log.info("no listeners; stopping mp3 encoder")
                    if reader:
                        reader.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await reader
                        reader = None
                    await self._kill_ffmpeg()

                proc = self._ffmpeg
                if proc is None:
                    continue  # nobody listening: the fifo is drained, nothing encoded
                if proc.returncode is not None:
                    raise RuntimeError("ffmpeg exited")
                if len(data) < CHUNK_BYTES:
                    data += silence[: CHUNK_BYTES - len(data)]
                assert proc.stdin
                proc.stdin.write(data)
                await proc.stdin.drain()
        finally:
            if reader:
                reader.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await reader
            if fd is not None:
                os.close(fd)
