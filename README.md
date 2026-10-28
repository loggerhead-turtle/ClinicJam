# ClinicJam 🎵

A self-contained office music jam that runs on a Raspberry Pi 4/5.

Guests join a private Wi-Fi network with their phones, a captive portal
pops open the jam app — no app installs, no accounts, no logins — and they
search Spotify's catalog, queue songs, upvote what should play next, and
vote to skip. The Pi plays the music through one office Spotify Premium
account and streams it, perfectly in sync, to a group of Google Nest
speakers around the office.

## Why it's compliance-friendly

The whole point of this design is that **nothing here touches the company
network**:

- The Pi gets its own internet from a **USB cellular modem** (or any
  uplink you plug in — Ethernet, or even the company's own guest Wi-Fi
  through a second radio, see [docs/NETWORK.md](docs/NETWORK.md#alternative-uplink-joining-another-wi-fi-network-eg-the-office-guest-network)).
  The office network is never used and never shared onward.
- Guest phones join the Pi's private Wi-Fi and get **zero internet
  access** — the firewall forwards nothing for them. The Wi-Fi is a
  kiosk for the jam app, not an internet hotspot.
- The Nest speakers also live on the Pi's private Wi-Fi. The music itself
  is streamed **locally** from the Pi; the speakers are only allowed a
  trickle of internet (via the Pi's own uplink) for Google's device
  check-in.

## How it works

```
                        ┌────────────────────────────┐
   USB cellular modem ──┤        Raspberry Pi        │
   (Mint Mobile etc.)   │                            │
   Pi + speakers only   │  hostapd ── private Wi-Fi  │)))  "ClinicJam" SSID
                        │  dnsmasq ── DHCP + portal  │
                        │  nftables ─ walled garden  │
                        │                            │
                        │  librespot ← Spotify       │      guests' phones
                        │      │ raw audio (PCM)     │)))   captive portal →
                        │  ffmpeg → /stream.mp3      │      queue • vote • skip
                        │      │                     │
                        │  FastAPI ─ jam app + API   │)))   5× Google Nest
                        └────────────────────────────┘      (one Cast group,
                                                             synced audio)
```

One office **Spotify Premium** account powers search and playback. The Pi
appears to Spotify as a Connect device (librespot); the jam server tells
it what to play, captures the raw audio, encodes it to MP3, and casts the
live stream to a Chromecast **speaker group** containing all five Nests —
the group keeps them in sync automatically.

## Features

- 📱 **Guest app** (captive portal): search, add to queue, upvote, vote-skip,
  live now-playing with album art — updates pushed over WebSocket.
- 🔐 **Two-tier PINs**: a shared **jam PIN** (post it at the front desk)
  lets people search, queue, and vote — nothing else. The **admin PIN**
  unlocks the full host panel: settings, speakers, moderation, bans.
- 🗳️ **Queue modes**: most-votes-first or strict first-come-first-served.
- ⏭️ **Vote to skip** with a configurable listener threshold.
- 🎛️ **Host panel** (`/admin`, PIN-protected): start/stop the jam,
  pause/skip, remove songs, block rowdy devices, pick speakers, volume.
- 🚫 **Explicit filter** — keep it waiting-room safe.
- 📻 **Fallback playlist** — when the guest queue is empty, an office
  playlist keeps the music going.
- 🖥️ **Optional touchscreen kiosk** — attach a touch display to the Pi
  and it boots straight into a big-button jam UI with an on-screen
  keyboard, so nobody even needs their phone
  ([docs/KIOSK.md](docs/KIOSK.md)).

## Quick start

**New to this?** [docs/QUICKSTART.md](docs/QUICKSTART.md) is a complete,
no-assumptions walkthrough from "blank SD card" to "music playing on the
Nest speakers," written for a Pi 4 + USB Wi-Fi dongle uplink setup.
The condensed version:

1. Flash **Raspberry Pi OS Bookworm (64-bit)** to a Pi 4 or Pi 5, plug in
   your uplink (USB cellular modem or Ethernet), and run:

   ```sh
   curl -fsSL https://raw.githubusercontent.com/loggerhead-turtle/ClinicJam/claude/office-clinic-jam-app-voejwh/install/install.sh | sudo bash
   ```

2. Create a (free) Spotify developer app and put its keys in
   `/etc/clinicjam/config.toml` → [docs/SPOTIFY_SETUP.md](docs/SPOTIFY_SETUP.md)

3. Join the jam Wi-Fi with your phone, open `http://10.24.7.1/admin`,
   log in with your PIN, and connect the office Spotify Premium account.

4. Put the Nest speakers on the jam Wi-Fi, make a speaker group, and
   allowlist their MACs → [docs/NEST_SETUP.md](docs/NEST_SETUP.md)

5. Press **Start the jam**. 🎉

6. *(Optional)* Attach a touchscreen and run
   `sudo /opt/clinicjam/install/setup-kiosk.sh` for the walk-up kiosk
   → [docs/KIOSK.md](docs/KIOSK.md)

## Repository layout

| Path | What it is |
|---|---|
| `server/clinicjam/` | FastAPI app: queue, Spotify client, player loop, cast control, live MP3 streamer |
| `server/static/` | Guest app + host panel (plain HTML/JS, captive-portal friendly) |
| `install/` | One-line installer, network config renderer, systemd units |
| `docs/` | Setup and architecture guides |
| `config.example.toml` | All configuration, one file |

## Documentation

- [docs/QUICKSTART.md](docs/QUICKSTART.md) — the full step-by-step, start to finish
- [docs/HARDWARE.md](docs/HARDWARE.md) — shopping list and physical setup
- [docs/NETWORK.md](docs/NETWORK.md) — the walled garden, captive portal, and firewall in detail
- [docs/SPOTIFY_SETUP.md](docs/SPOTIFY_SETUP.md) — API keys and account linking
- [docs/NEST_SETUP.md](docs/NEST_SETUP.md) — speakers, the Cast group, MAC allowlist
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — how the pieces fit together
- [docs/SECURITY.md](docs/SECURITY.md) — the security/reliability audit and what it changed

## Honest limitations

- **Spotify only** for now. Nest speakers only accept Chromecast streams,
  and Apple Music/YouTube Music don't permit third-party server-side
  playback — a single Spotify Premium account is the one path that is
  reliable *and* within the rules. Guests don't need any account.
- One jam at a time, one audio zone (all five speakers play the same thing).
- The live stream buffers a few seconds; skips take a moment to reach the
  speakers.

## Development

```sh
cd server
python3 -m venv venv && venv/bin/pip install -r requirements.txt pytest
venv/bin/pytest tests/            # queue/vote logic tests
venv/bin/python -m clinicjam.main # run the web app locally
```
