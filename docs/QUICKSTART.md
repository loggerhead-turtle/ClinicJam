# Quick start: from blank SD card to music playing on the Nest speakers

This is the complete path for your setup: a Raspberry Pi 4, a USB Wi-Fi
dongle, and 5 Google Nest Audio speakers. No prior Raspberry Pi
experience needed. Total time: about 45 minutes, most of it waiting for
things to install.

**A note before you start:** there's no pre-built "ClinicJam SD card
image" to download — what you're downloading in Step 1 is the official,
generic Raspberry Pi operating system from Raspberry Pi themselves.
Step 4 is the one command that turns a blank Pi into ClinicJam. That's
genuinely the simplest this gets: one official tool, one command.

## What you need in front of you

- The Raspberry Pi 4 and its power supply
- A microSD card (8GB or bigger) and a way to plug it into your computer
  (a USB adapter if your computer has no SD slot)
- Your USB Wi-Fi dongle
- A computer connected to the internet
- Your office Wi-Fi name and password (temporarily, just for Step 4)
- The name/password you want the **guest jam Wi-Fi** to use
- A 4-8 digit PIN for yourself (the "admin PIN") and optionally a
  separate one to hand out to coworkers (the "jam PIN")

Your Bluetooth dongle isn't used by this setup — ClinicJam reaches the
Nest speakers over Wi-Fi, which is more reliable for continuous audio
than Bluetooth would be for multiple speakers. Keep it aside.

---

## Step 1 — Download Raspberry Pi Imager

Go to **raspberrypi.com/software** and download **Raspberry Pi Imager**
for your computer (Windows/Mac/Linux). Install it and open it.

## Step 2 — Flash the SD card

1. Plug the microSD card into your computer.
2. In Raspberry Pi Imager:
   - **Choose Device** → Raspberry Pi 4
   - **Choose OS** → "Raspberry Pi OS (other)" → **Raspberry Pi OS Lite (64-bit)**
     (Lite is correct — no desktop needed, and it's lighter on the Pi)
   - **Choose Storage** → your SD card
3. Click the **gear icon** (⚙) in the bottom corner — this is the
   important part:
   - Set a **hostname**, e.g. `clinicjam`
   - **Enable SSH**, choose "Use password authentication", set a
     username and password you'll remember
   - **Configure wireless LAN**: enter your **office Wi-Fi** name and
     password (this is temporary — just so the Pi can reach the internet
     the very first time; it will switch roles later in Step 4)
   - Set your Wi-Fi country
   - Save, then click **Write**
4. Wait for it to finish (5-15 minutes), then move the SD card to the Pi.

## Step 3 — Boot it and connect

1. Put the SD card in the Pi, plug in power. Wait about 2 minutes for
   first boot.
2. On your computer, open a terminal (Mac: Terminal app; Windows: use
   PowerShell, or install [PuTTY](https://www.putty.org/) if you prefer
   a graphical SSH client) and connect:

   ```
   ssh yourusername@clinicjam.local
   ```

   (use the username you set in Step 2; if `clinicjam.local` doesn't
   resolve, check your router's device list for the Pi's IP address and
   use that instead, e.g. `ssh yourusername@192.168.1.47`)
3. Plug your **USB Wi-Fi dongle** into the Pi now, before continuing.

## Step 4 — Run the installer (the one command that builds ClinicJam)

In that SSH terminal:

```
curl -fsSL https://raw.githubusercontent.com/loggerhead-turtle/ClinicJam/claude/office-clinic-jam-app-voejwh/install/install.sh | sudo bash
```

This takes 5-10 minutes. Partway through, it asks you some plain-English
questions — here's what to answer for your setup:

| It asks... | What to enter |
|---|---|
| Wi-Fi network name for guests | Whatever you want people to see and join, e.g. `OfficeJam` |
| Admin PIN for the host panel | A 4-8 digit PIN only you know |
| Jam PIN guests enter to join | A 4-8 digit PIN you can post on the wall (or leave blank for no PIN) |
| Wi-Fi country code | Your two-letter country code, e.g. `US` |
| Wi-Fi password for guests | **Set one** — an open network sends everything over the air in plain text, including the PIN you just picked. This one you make up fresh; it's the password for the new jam network, nothing to do with your office Wi-Fi |

When it finishes, it prints a summary with your Pi's address (normally
`http://10.24.7.1/`) — keep that terminal window open, you'll see it again.

## Step 5 — Point the Pi's own internet at your USB dongle

Still in the SSH session:

```
sudo nano /etc/clinicjam/config.toml
```

Find the `[uplink]` section near the top and change it to:

```toml
[uplink]
mode = "wifi_client"

[uplink.wifi_client]
interface = "wlan1"
ssid = "YourOfficeWifiName"
psk = "YourOfficeWifiPassword"
```

(If your office network has no password, leave `psk = ""`.)

Save and exit (`Ctrl+O`, `Enter`, `Ctrl+X`), then run:

```
sudo /opt/clinicjam/install/setup-uplink.sh
```

This is the handoff moment: from here on, the Pi reaches the internet
through your **USB dongle** joining the office Wi-Fi, while its **built-in**
radio broadcasts the new guest "jam" network from Step 4. Two separate
networks, two separate radios — nobody on the jam network gets internet
access, and the office network never sees guest traffic.

**If your office Wi-Fi has a "click to accept" page** before it gives
real internet (common on guest networks), turn on the auto-accept helper
— it almost always works unmodified:

```
cp /opt/clinicjam/docs/portal_click.example.py /etc/clinicjam/portal_click.py
sudo systemctl restart clinicjam-portal-watch
```

Give it a minute, then check it actually got online:

```
curl -I http://neverssl.com
```

If that prints a real web page's headers (not an error), you're online.
If you get stuck here, see `docs/NETWORK.md` for the fallback options.

## Step 6 — Connect the Spotify account

1. From a laptop or phone **on the new jam Wi-Fi**, open
   `http://10.24.7.1/admin` in a browser.
2. Enter your admin PIN.
3. You'll see a note that Spotify needs an API key first — follow
   `docs/SPOTIFY_SETUP.md` (5 minutes, one-time, free). Then come back to
   this page.
4. Click **Connect Spotify**, log in with the office's Spotify **Premium**
   account (Premium is required), and paste the resulting link back in
   as instructed on screen.

## Step 7 — Bring the 5 Nest speakers onto the jam Wi-Fi

1. Open the **Google Home** app on your phone (your phone should be on
   the jam Wi-Fi for this).
2. For each of the 5 Nest Audio speakers: if it's already set up on a
   different network, remove it from that network first, then add it
   again as a new device — this time picking your new jam Wi-Fi.
3. Once all 5 are on the jam Wi-Fi, in Google Home create a **speaker
   group** containing all 5 (this is what makes them play in sync).
   Name it something like "Office".
4. Back in the ClinicJam admin panel (`http://10.24.7.1/admin`), find the
   **Speakers** section, pick that group from the list, and set a volume.

Full walkthrough with troubleshooting: `docs/NEST_SETUP.md`.

## Step 8 — Start the jam

In the admin panel, click **Start the jam**. Give it about 10 seconds,
and you should hear music on all 5 speakers. From here:

- Anyone on the jam Wi-Fi can go to `http://10.24.7.1/` to search songs,
  add them to the queue, and vote.
- You control everything (pause, skip, settings, volume, who's banned)
  from `http://10.24.7.1/admin`.
- If you set up the touchscreen kiosk (`docs/KIOSK.md`), people can do
  the same thing by walking up to the screen instead of using their phone.

## If something doesn't work

```
systemctl status 'clinicjam-*'      # see which service is unhappy
journalctl -u clinicjam-web -f      # watch the main app's logs live
```

Most first-time snags are one of: the USB dongle not being in `wlan1`
(check with `ip link`, adjust `config.toml` if it's named differently),
Spotify not fully linked yet (Step 6), or the Nest speakers still being
on the old Wi-Fi network (Step 7). `docs/NETWORK.md`, `docs/SPOTIFY_SETUP.md`,
and `docs/NEST_SETUP.md` each have a troubleshooting section for their part.
