"""Local audio streaming: librespot PCM -> MP3 -> HTTP for the Nest group.

librespot (the Spotify Connect endpoint) writes raw 44.1 kHz s16le stereo
PCM into a FIFO. We read it non-blocking at a real-time pace, padding with
silence whenever Spotify is paused or between tracks, and feed the result
to ffmpeg for MP3 encoding. The encoded stream is fanned out to HTTP
subscribers at /stream.mp3 — the Chromecast speaker group is the one
subscriber that matters, and the continuous silence keeps it from
dropping the session during quiet moments.
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


class Broadcaster:
    """Fan a byte stream out to any number of HTTP subscribers."""

    def __init__(self, max_queue: int = 256):
        self.subscribers: set[asyncio.Queue[bytes]] = set()
        self.max_queue = max_queue

    def publish(self, chunk: bytes) -> None:
        for q in list(self.subscribers):
            if q.qsize() >= self.max_queue:
                # Slow reader: drop the oldest chunk instead of ballooning.
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()
            q.put_nowait(chunk)

    async def subscribe(self):
        q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=self.max_queue + 8)
        self.subscribers.add(q)
        try:
            while True:
                yield await q.get()
        finally:
            self.subscribers.discard(q)


class AudioStreamer:
    def __init__(self, fifo_path: str, bitrate: str):
        self.fifo_path = fifo_path
        self.bitrate = bitrate
        self.broadcaster = Broadcaster()
        self._task: asyncio.Task | None = None
        self._ffmpeg: asyncio.subprocess.Process | None = None

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

    async def _kill_ffmpeg(self) -> None:
        if self._ffmpeg and self._ffmpeg.returncode is None:
            self._ffmpeg.kill()
            with contextlib.suppress(ProcessLookupError):
                await self._ffmpeg.wait()
        self._ffmpeg = None

    async def _run(self) -> None:
        while True:
            try:
                await self._pump_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("audio pipeline crashed; restarting in 3s")
            await asyncio.sleep(3)

    async def _pump_once(self) -> None:
        self._ffmpeg = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-hide_banner", "-loglevel", "error",
            "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS),
            "-i", "pipe:0",
            "-f", "mp3", "-b:a", self.bitrate,
            "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        reader = asyncio.create_task(self._read_encoded(), name="mp3-reader")
        try:
            await self._feed_pcm()
        finally:
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reader
            await self._kill_ffmpeg()

    async def _read_encoded(self) -> None:
        assert self._ffmpeg and self._ffmpeg.stdout
        while True:
            chunk = await self._ffmpeg.stdout.read(4096)
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
        assert self._ffmpeg and self._ffmpeg.stdin
        fd = self._open_fifo()
        silence = b"\x00" * CHUNK_BYTES
        next_deadline = time.monotonic()
        try:
            while self._ffmpeg.returncode is None:
                next_deadline += CHUNK_SECONDS
                delay = next_deadline - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
                else:
                    # We fell behind (CPU stall); resynchronize the clock.
                    next_deadline = time.monotonic()

                data = b""
                if fd is None:
                    fd = self._open_fifo()
                if fd is not None:
                    try:
                        data = os.read(fd, CHUNK_BYTES)
                    except BlockingIOError:
                        data = b""
                    except OSError:
                        os.close(fd)
                        fd = None
                if len(data) < CHUNK_BYTES:
                    data += silence[: CHUNK_BYTES - len(data)]

                self._ffmpeg.stdin.write(data)
                await self._ffmpeg.stdin.drain()
        finally:
            if fd is not None:
                os.close(fd)
