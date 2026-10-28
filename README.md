# ClinicJam 🎵

A self-contained office music jam that runs on a Raspberry Pi 4/5 and plays
through Google Nest speakers.

Guests join a private Wi-Fi network with their phones, a captive portal
pops open the jam app — no app installs, no accounts, no logins — and they
search Spotify's catalog, queue songs, upvote what should play next, and
vote to skip. The Pi plays the music through one office Spotify Premium
account and streams it, perfectly in sync, to a Cast speaker group of all
the Nest speakers around the office.

**New here? Start with [docs/QUICKSTART.md](docs/QUICKSTART.md)** — download
one file, flash it to an SD card, edit one settings file, done.

## Why it's compliance-friendly

Nothing here touches the company network, in either direction:

- The Pi gets its own internet from an **Ethernet jack, a USB cellular
  modem, or a USB Wi-Fi dongle** that joins the office/guest Wi-Fi like any
  laptop. That connection is used by the Pi alone and never shared onward.
- Guest phones join the Pi's private Wi-Fi and get **zero internet**: no
  forwarding, no DNS (every name resolves to the Pi), no access to anything
  on the Pi except the jam app itself, and no access to each other or to
  the speakers.
- The Nest speakers also live on the Pi's private Wi-Fi. Music is streamed
  **locally** from the Pi; the speakers are allowed only a trickle of
  internet (Google's ports, public addresses) for their check-in.
- The jam app is unreachable from the office-network side of the Pi.

[docs/SECURITY_AUDIT.md](docs/SECURITY_AUDIT.md) lists every finding of the
security review and what was done about it.

## How it works

```
                        ┌────────────────────────────────┐
   Ethernet / USB modem │        Raspberry Pi            │
   / USB Wi-Fi dongle ──┤  (its own uplink; never shared)│
                        │                                │
                        │  hostapd ── private Wi-Fi      │)))  "ClinicJam" SSID (WPA2)
                        │  dnsmasq ── DHCP + DNS sinkhole│
                        │  nftables ─ walled garden      │
                        │                                │
                        │  librespot ← Spotify           │      guests' phones
                        │      │ raw audio (PCM)         │)))   captive portal →
                        │  ffmpeg → /stream.mp3          │      queue • vote • skip
                        │      │                         │
                        │  FastAPI ─ jam app + API       │)))   Google Nest speakers
                        └────────────────────────────────┘      (one Cast group,
                                                                 synced audio)
```

## Features

- 📱 **Guest app** (captive portal): search, add to queue, upvote, vote-skip,
  live now-playing with album art — updates pushed over WebSocket.
- 🔐 **Two-tier PINs**: a shared **jam PIN** lets people search, queue, and
  vote; the **admin PIN** unlocks the host panel. Brute-force lockout, signed
  sessions, logout.
- 🗳️ **Queue modes**: most-votes-first or strict first-come-first-served.
- ⏭️ **Vote to skip** with a configurable listener threshold.
- 🎛️ **Host panel** (`/admin`): start/stop, pause/skip, remove songs, block
  a device (by hardware address, not just browser), pick speakers, volume,
  **setup mode** for enrolling speakers, internet allowlist.
- 🔊 **Zero-config speakers**: speakers that join the jam Wi-Fi are
  allowlisted automatically; one Spotify login links both the account and
  the player — no developer app needed.
- 🚫 **Explicit filter** and 📻 **fallback playlist** so the waiting room is
  never silent or awkward.
- 💤 **Frugal**: the MP3 encoder only runs while speakers are listening;
  everything is bounded for a 2 GB Pi.
- 🖥️ **Optional touchscreen kiosk** ([docs/KIOSK.md](docs/KIOSK.md)).

## Quick start

1. Download the newest `clinicjam-*.img.xz` from the
   [Releases](https://github.com/loggerhead-turtle/ClinicJam/releases/latest) page.
2. Flash it with Raspberry Pi Imager (**Use custom**, say **No** to Imager's
   customisation).
3. Open the `bootfs` drive, edit **`clinicjam-settings.txt`** (Wi-Fi name and
   password, PINs, optionally the office Wi-Fi for the dongle). Eject.
4. Boot the Pi with Ethernet or the dongle plugged in. Join the jam Wi-Fi
   with your phone, open `http://10.24.7.1/admin`.
5. **Setup mode** on → **Open Spotify login** → paste the address back →
   add the speakers with Google Home → create a speaker group → pick it →
   **Start the jam**.

Every click of that is spelled out with screenshots-in-words in
[docs/QUICKSTART.md](docs/QUICKSTART.md).

Installing onto an existing Raspberry Pi OS instead of flashing the image:

```sh
curl -fsSL https://raw.githubusercontent.com/loggerhead-turtle/ClinicJam/claude/office-clinic-jam-app-voejwh/install/install.sh | sudo bash
```

## Repository layout

| Path | What it is |
|---|---|
| `server/clinicjam/` | FastAPI app: queue, Spotify client, player loop, cast control, live MP3 streamer, auth |
| `server/static/` | Guest app, host panel, kiosk (plain HTML/JS, captive-portal friendly) |
| `install/` | Installer, network config renderer, privileged firewall helper, systemd units |
| `image/` | Builds the flashable image (`build-image.sh`), the settings file template |
| `.github/workflows/` | CI that builds the image and attaches it to a release |
| `docs/` | Setup guides, architecture, the security audit |
| `config.example.toml` | All configuration, one file (filled in by the settings file on the image) |

## Documentation

- [docs/QUICKSTART.md](docs/QUICKSTART.md) — **start here**: flash, settings file, first boot, speakers
- [docs/HARDWARE.md](docs/HARDWARE.md) — shopping list and physical setup
- [docs/NETWORK.md](docs/NETWORK.md) — the walled garden, captive portal, firewall and uplink options
- [docs/SPOTIFY_SETUP.md](docs/SPOTIFY_SETUP.md) — how the login works; optional developer app
- [docs/NEST_SETUP.md](docs/NEST_SETUP.md) — speakers, the Cast group, setup mode
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — how the pieces fit together
- [docs/SECURITY_AUDIT.md](docs/SECURITY_AUDIT.md) — findings and fixes
- [docs/KIOSK.md](docs/KIOSK.md) — optional touchscreen

## Honest limitations

- **Spotify only.** Nest speakers only accept Cast streams, and Apple
  Music/YouTube Music don't permit third-party server-side playback — a
  single Spotify Premium account is the one path that is reliable *and*
  within the rules. Guests don't need any account.
- One jam at a time, one audio zone (all speakers play the same thing).
- The live stream buffers a few seconds; skips take a moment to reach the
  speakers.
- Bluetooth is not used: Cast is Wi-Fi only, and Bluetooth could not keep
  several speakers in sync.

## Development

```sh
cd server
python3 -m venv venv && venv/bin/pip install -r requirements.txt pytest
venv/bin/pytest                   # auth, hardening, queue/vote logic
venv/bin/python -m clinicjam.main # run the web app locally (config.example.toml)
```

Build the image locally (Debian/Ubuntu, needs root and `qemu-user-static`):

```sh
sudo image/build-image.sh         # -> image/out/clinicjam-<version>.img.xz
```
