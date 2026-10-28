# Spotify setup

You need one thing: the **office Spotify Premium account**. Premium is
required (Connect playback control is a Premium feature). Guests never log
in to anything.

## The one-paste login (default)

1. From a phone on the jam Wi-Fi, open `http://10.24.7.1/admin` and log in
   with the admin PIN.
2. The login page needs internet: turn on **setup mode** (Speakers section)
   or use mobile data for the next step.
3. **Connect Spotify → Open Spotify login**, sign in with the office account,
   approve.
4. The browser lands on a page that can't load:
   `http://127.0.0.1:8898/login?code=…` — **that's expected.** Copy the full
   address from the address bar.
5. Paste it into the box in the host panel, **Finish**.

Behind the scenes this is Spotify's standard authorization-code flow with
PKCE using Spotify's own desktop client id (the same one the open-source
player, librespot, uses). The resulting token signs the player in once;
after that the player keeps its own cached credentials. The Web API
(search, "play this on the Pi") uses the same token, refreshed as needed,
and the refresh token is stored on the Pi in a root-and-service-only file.

You won't need to do this again unless you **Disconnect account** in the
panel, revoke access at spotify.com, or reflash the card.

## Optional: your own developer app

If you'd rather the Web API traffic be attributed to an app you own (or if
the built-in login ever stops working for the API), create a free app at
<https://developer.spotify.com/dashboard> with redirect URI exactly
`http://127.0.0.1:8888/callback`, and put its keys in the Pi's config:

```toml
[spotify]
client_id = "your-client-id"
client_secret = "your-client-secret"
```

```sh
sudo systemctl restart clinicjam-web
```

The host panel then shows two steps: **Connect Spotify** (the API, your
app) and **Link the player** (librespot, same copy-paste dance). Note that
Spotify restricts new apps to a handful of allowlisted users in
"development mode" — add the office account in the app's settings.

## The Spotify Connect device

The Pi registers as a Connect device named **ClinicJam** (`device_name`),
with discovery on the local network **disabled** so nobody can hijack it
with their own account. If the host panel says the player is "linked, not
visible":

```sh
sudo systemctl status clinicjam-librespot
sudo journalctl -u clinicjam-librespot -e
```

"waiting for the Spotify player to be linked" means the login paste hasn't
happened yet. A login failure means the token expired before use — do the
login again. It typically appears within ~20 seconds of a successful login.

## A note on other services

Guests often ask about Apple Music / YouTube Music. The honest answer:
Google Nest speakers only accept Cast streams, and neither Apple nor
Google licenses third-party server-side playback of their catalogs. One
Spotify Premium account is the approach that works reliably and stays
within everyone's terms of service.
