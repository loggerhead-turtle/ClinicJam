# Architecture

## The audio path (the interesting part)

```
Spotify cloud ──(uplink)──► librespot ──► /run/clinicjam/audio.fifo (raw PCM)
                            (Spotify                │
                             Connect                ▼
                             device)      AudioStreamer (clinicjam/stream.py)
                                          reads the fifo NON-BLOCKING at a
                                          real-time pace, padding silence when
                                          Spotify is paused / between tracks
                                                    │ continuous PCM
                                                    ▼
                                                 ffmpeg ──► MP3 320k
                                                    │
                                                    ▼
                                          Broadcaster ──► GET /stream.mp3
                                                              │
                                                              ▼
                                            Chromecast speaker group (5× Nest)
```

Key decisions:

- **Silence padding** keeps the MP3 stream continuous forever. Cast
  devices drop live streams that stall; ours never stalls, so the group
  stays latched between songs, during pauses, and while the queue is empty.
- **One cast target = the speaker group.** Google's group implementation
  does multi-room sync far better than five individual casts ever could.
- The stream is a couple of seconds behind "real time" (encoder + cast
  buffering). Fine for an office; not a DJ monitor.

## The control path

```
guest phone ──HTTP/WS──► FastAPI (main.py)
                           │        │
                        Jam (jam.py)│              queue, votes, skip votes,
                           │        │              devices, bans — pure logic
                           ▼        ▼
                       Player (player.py) ──────► Spotify Web API: "play track X
                           ▲                       on Connect device ClinicJam"
                           │ end_of_track
                       librespot --onevent hook (bin/onevent.sh → localhost POST)
```

- The **player loop** owns playback: pops the top of the queue (or a
  shuffled fallback-playlist track), starts it via the Web API, and waits
  for the end-of-track event. API polling acts as a backstop if an event
  is missed.
- **Vote-skip** and admin skip just set the same "track ended" flag.
- State pushes to phones over WebSocket on every change plus a 10 s
  heartbeat; the UI also works with plain fetches if WS drops.

## Processes (systemd)

| Unit | What |
|---|---|
| `clinicjam-network` | Static IP on the AP interface |
| `clinicjam-hostapd` | The Wi-Fi AP |
| `clinicjam-dnsmasq` | DHCP/DNS/portal hints for the jam subnet |
| `clinicjam-firewall` | nftables walled garden |
| `clinicjam-web` | FastAPI app: guest UI, admin, API, `/stream.mp3`, cast control |
| `clinicjam-librespot` | Spotify Connect endpoint writing PCM to the fifo |

`clinicjam-web` runs as the unprivileged `clinicjam` user. The fifo and
`/run/clinicjam` come from tmpfiles.d so both services agree on paths.

## State

- `/etc/clinicjam/config.toml` — install-time config (network, keys, PIN).
- `/var/lib/clinicjam/state.json` — runtime state that survives reboots:
  Spotify refresh token, admin-tunable settings (queue mode, explicit
  filter, skip ratio, fallback playlist, cast target), banned devices.
- The queue itself is in-memory by design: a reboot mid-jam clears the
  queue but the jam picks right back up from the fallback playlist.

## Guest identity

No accounts: each phone generates a random device id kept in
localStorage, sends it as `X-Device-Id`, and picks a display name. Votes
and per-user limits key off that id; the admin can ban an id. It's honest
about its threat model — this keeps coworkers polite, it doesn't stop a
determined attacker (who could, at worst, ...queue songs).
