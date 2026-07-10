# Spotify setup

You need two things, both one-time:

1. A **Spotify developer app** (free) — gives the jam server API access
   for search and playback control.
2. The **office Spotify Premium account** linked to the jam — the account
   the music actually plays from. Premium is required (Connect playback
   control is a Premium feature). Guests never log in to anything.

## 1. Create the developer app

1. Go to <https://developer.spotify.com/dashboard> and log in (any
   account works, but using the office account keeps things tidy).
2. **Create app**. Name: `ClinicJam`. Redirect URI:
   `http://127.0.0.1:8888/callback` — exactly this; Spotify only allows
   plain-HTTP redirects on the loopback address, which is why the linking
   flow below has a copy-paste step.
3. In the app's settings, copy the **Client ID** and **Client Secret**
   into the Pi's config:

   ```sh
   sudo nano /etc/clinicjam/config.toml
   ```

   ```toml
   [spotify]
   client_id = "your-client-id"
   client_secret = "your-client-secret"
   ```

   ```sh
   sudo systemctl restart clinicjam-web
   ```

## 2. Link the office account

1. From a phone or laptop on the jam Wi-Fi, open `http://10.24.7.1/admin`
   and log in with your admin PIN.
2. In **Connect Spotify**, tap **Open Spotify login** and sign in with
   the office Premium account. Note: your device needs internet for the
   Spotify login page — do this from a phone with mobile data, or
   temporarily allowlist your laptop's MAC like a speaker.
3. After you approve, the browser lands on a broken page at
   `http://127.0.0.1:8888/callback?code=...` — **that's expected**.
   Copy the full address from the address bar.
4. Paste it into the box in the admin panel and hit **Finish**.

The refresh token is stored on the Pi; you won't need to do this again
unless you revoke access.

## The Spotify Connect device

The Pi announces itself as a Spotify Connect device named **ClinicJam**
(configurable as `device_name`). If the admin panel says the device is
"not visible", check:

```sh
sudo systemctl status clinicjam-librespot
sudo journalctl -u clinicjam-librespot -e
```

It typically appears within ~10 seconds of the service starting, once the
linked account looks for devices.

## A note on other services

Guests often ask about Apple Music / YouTube Music. The honest answer:
Google Nest speakers only accept Chromecast streams, and neither Apple
nor Google licenses third-party server-side playback of their catalogs.
One Spotify Premium account is the approach that works reliably and stays
within everyone's terms of service.
