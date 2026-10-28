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
- **The encoder is on demand.** ffmpeg runs only while something is pulling
  `/stream.mp3` (plus a 30 s grace); the FIFO is always drained so librespot
  never blocks. Idle nights cost no CPU.
- **The stream is only served to speakers and the host**, and to at most
  `max_stream_clients` at once.
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
  for the end-of-track event (`end_of_track`, `stopped`, `unavailable`).
  API polling acts as a backstop if an event is missed. The event hook
  authenticates with a per-boot token in `/run/clinicjam/hook.token`.
- **Logging in**: librespot only accepts OAuth tokens. The admin panel runs
  a PKCE login with Spotify's desktop client id, writes the token to
  `/var/lib/clinicjam/librespot-token`, and asks the root helper to restart
  the player; `run-librespot.sh` signs in with it once and librespot caches
  reusable credentials from then on. Discovery (zeroconf) is off.
- **Vote-skip** and admin skip just set the same "track ended" flag.
- State pushes to phones over WebSocket on every change plus a 10 s
  heartbeat; the UI also works with plain fetches if WS drops.

## Processes (systemd)

| Unit | What |
|---|---|
| `clinicjam-firstboot` | Applies `clinicjam-settings.txt` from the boot partition (every boot, if changed) |
| `clinicjam-network` | Bridge + static IPs on the jam interface |
| `clinicjam-firewall` | nftables walled garden (validated, before hostapd) |
| `clinicjam-hostapd` | The Wi-Fi AP (+ bridge hairpin) |
| `clinicjam-dnsmasq` | DHCP + sinkhole DNS for guests |
| `clinicjam-dns-nest` | Real resolver for the speakers |
| `clinicjam-web` | FastAPI app: guest UI, admin, API, `/stream.mp3`, cast control |
| `clinicjam-librespot` | Spotify Connect player writing PCM to the fifo |
| `clinicjam-fw.path/.service` | Root helper: processes allowlist / setup-mode / restart requests |
| `clinicjam-portal-watch` | Optional captive-portal auto-accept for the uplink |

`clinicjam-web` and `clinicjam-librespot` run as the unprivileged
`clinicjam` user under a strict systemd sandbox (no new privileges, no
capabilities, read-only system, memory caps). Anything that needs root
is a JSON request file dropped into `/run/clinicjam/fw-queue`; a path unit
wakes `clinicjam-fw`, which validates and applies it and publishes the
result in `/run/clinicjam/fw-state.json`. The fifo and `/run/clinicjam`
come from tmpfiles.d so all services agree on paths.

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
and per-user limits key off that id. Only devices that joined with the
jam PIN count as listeners (so the skip threshold can't be gamed from
outside), and a ban records the phone's hardware address too, so clearing
the browser doesn't lift it. Sessions are HMAC-signed cookies: nothing is
stored server-side, they expire, and they die when the PIN changes. It's
honest about its threat model — this keeps coworkers polite; a determined
attacker with the jam PIN could, at worst, queue songs.
