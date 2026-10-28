# Touchscreen kiosk

An optional wall/desk touchscreen on the Pi itself, so people can browse
the queue, add songs, and vote without pulling out their phones. Pairs
naturally with the official Raspberry Pi 7" Touch Display, but any
HDMI/DSI touchscreen works.

## Install

After the main installer has run:

```sh
sudo /opt/clinicjam/install/setup-kiosk.sh
```

That installs `cage` (a minimal Wayland kiosk compositor) and Chromium,
creates a `kiosk` user, and adds a `clinicjam-kiosk` systemd service that
boots the screen straight into `http://127.0.0.1:8080/kiosk` fullscreen.
No desktop environment is installed — this works on Raspberry Pi OS
**Lite** and adds only a few hundred MB.

## What's on the screen

- **Left**: now playing with album art and progress, a big
  *vote to skip* button, and the listener count.
- **Right**: the live queue with upvote buttons, and a search bar that
  opens a built-in on-screen keyboard (no OS keyboard integration to
  babysit — it's part of the page).
- **Gear icon** (bottom-left): host controls. Entering the **admin PIN**
  on the on-screen PIN pad reveals pause / resume / skip / stop-jam
  buttons plus a **Full admin panel** button that opens `/admin` right on
  the touchscreen (settings, speakers, moderation, bans). Tap the gear
  again to lock: that drops the host session on the Pi, not just the
  buttons. Host sessions on the kiosk expire after 15 minutes anyway.

If a **jam PIN** is configured (`guest_pin` in config.toml), the kiosk
boots locked: a PIN pad covers the screen until someone enters the shared
jam PIN (or the admin PIN). Once unlocked it stays unlocked until the
kiosk restarts — the browser runs in incognito mode, so nothing persists
across a restart (`sudo systemctl restart clinicjam-kiosk` re-locks it). Guests at the kiosk can search, queue, and vote, but
settings always require the admin PIN.

The kiosk counts as **one voter** — it shares a single device identity,
same as one phone, so a passerby can't multi-vote by hammering the screen.

## Managing it

```sh
systemctl status clinicjam-kiosk           # health
sudo systemctl restart clinicjam-kiosk     # reload the browser
sudo systemctl disable --now clinicjam-kiosk  # turn the kiosk off
```

The launcher waits for the jam server to answer before starting the
browser, so cold boots never flash an error page.

## Screen tweaks

- **Rotation** (official 7" display upside-down in many stands):
  add `lcd_rotate=2` to `/boot/firmware/config.txt`.
- **Brightness** (official display):
  `echo 128 | sudo tee /sys/class/backlight/*/brightness` (0–255).
- **Keeping the screen on**: cage doesn't blank the screen by default.
  If your display sleeps anyway, add `consoleblank=0` to
  `/boot/firmware/cmdline.txt`.
