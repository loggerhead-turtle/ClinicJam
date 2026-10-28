# Security & reliability audit

A from-the-code audit of the ClinicJam server and installer, done before
putting this on an office network. Each finding below was reproduced
against the real app (not just reasoned about) before being fixed —
see `server/tests/test_security.py` and `server/tests/test_jam.py` for
the automated checks, which run in CI-equivalent form via `pytest`.

## Findings & fixes

### 1. No brute-force protection on the PIN logins (High)

`/api/admin/login` and `/api/login` took a 4-digit PIN with no attempt
limit. On a LAN, 10,000 guesses against a 4-digit PIN is fast — seconds,
not hours — and the admin PIN controls the Spotify account link, device
bans, and jam settings.

**Fix:** `server/clinicjam/security.py` adds `LoginRateLimiter` — 5 wrong
admin-PIN attempts (10 for the guest PIN, since it's meant to be shared
more freely) within 60 seconds locks that client IP out for 60 (admin) or
30 (guest) seconds. A correct PIN does **not** bypass an active lockout —
confirmed by test, since a bypassable lockout isn't a lockout.
**Recommendation:** set PINs to 6+ digits where convenient; the installer
now enforces 4-8 digits and rejects anything else.

### 2. Secrets stored world-readable on disk (High)

`/etc/clinicjam/config.toml` (Spotify client secret, both PINs) and
`/var/lib/clinicjam/state.json` (the Spotify **refresh token** — a
standing credential, not a short-lived one) were created with whatever
the default umask gave them, typically world-readable. Any other local
account on the Pi could read them.

**Fix:** the installer now:
- `chmod 700` on `/var/lib/clinicjam` (blocks traversal into the
  directory entirely for anyone but the `clinicjam` user and root — this
  alone protects `state.json` regardless of the file's own mode)
- `chmod 750` on `/etc/clinicjam`, group-owned by `clinicjam`, so the
  service user can still read it
- `chmod 640` on `config.toml` itself
- `StateStore.save()` now also explicitly `chmod`s `state.json` to
  owner-read/write-only as defense in depth, independent of the
  directory permissions

### 3. No same-origin check on state-changing requests (Medium)

Classic cross-site CSRF from the open internet isn't reachable here —
the walled-garden firewall gives guest phones zero internet access, so
an attacker's page can't even load on a guest's browser. But within the
jam Wi-Fi itself, one guest's phone *could* run a tiny local web server
and lure another guest's browser to a page that fires authenticated
requests at the jam API using the victim's session cookie — add junk to
the queue, flip settings if they'd captured an admin session, etc.

**Fix:** a same-origin check middleware rejects any `POST`/`PUT`/`DELETE`/
`PATCH` to `/api/*` whose `Origin` (or `Referer`) header names a
different host than the request's own `Host` header. Every legitimate
call from `app.js`/`kiosk.js`/`admin.js` is same-origin by construction,
so this has no effect on normal use — verified live: a forged
cross-origin POST gets `403` before it ever reaches login or queue logic,
a same-origin POST passes straight through, and `GET` requests (which
can't mutate anything) are untouched.

### 4. Cookies without an explicit `SameSite` attribute (Low)

Not exploitable on its own given finding #3's fix, but cheap to tighten:
both session cookies now set `samesite="lax"` explicitly rather than
relying on the framework default.

### 5. Guest Wi-Fi defaults to open (no encryption) (Medium — operational)

With the default empty `wpa2_passphrase`, the jam Wi-Fi is an open
802.11 network: **anything sent over the air is readable by anyone
nearby with a Wi-Fi adapter in monitor mode** — including PIN submissions
in plaintext, before any of the above fixes even come into play. This
isn't a bug in the code, it's a consequence of the default configuration,
worth calling out explicitly rather than leaving implicit.

**Fix:** the installer now explains this tradeoff in plain language and
prompts for a Wi-Fi password, defaulting to "set one" rather than
silently leaving the network open. (An open network is still supported
for anyone who's weighed the tradeoff and wants frictionless guest
join — just no longer the silent default experience.)

### 6. XSS review — no issues found

Checked every `innerHTML` assignment across `app.js`, `kiosk.js`, and
`admin.js` (grep, then manually traced each one). All of them either
insert static markup with no interpolated data, or interpolate only a
vote *count* (always an integer from `len(set)`, never free text). Every
place a track name, artist, or display name is rendered uses
`textContent`, which escapes automatically. Spotify track/artist names
are technically attacker-influenceable (anyone can title a track
anything), so this was worth checking carefully rather than assuming.

### 7. Install script command-injection review — no issues found

`install.sh`'s `sed` substitutions only escape the sed delimiter
(`/`), not shell metacharacters — but values are only ever substituted
into a static text file via `sed`, never passed to `eval` or a
re-parsed shell string, so there's no code-execution vector; a value
with unusual characters could at worst produce a malformed
`config.toml` (a correctness problem, not a security one). PIN inputs
are now validated as digits-only regardless (see #1), which closes even
that. `setup-uplink.sh` passes Wi-Fi SSID/PSK values to `nmcli` as
regular quoted argv words (not through a re-parsed shell string), so
embedded special characters can't break out of that invocation either.
`render_network.py`'s MAC-address allowlist already validates each
entry against a strict regex before it reaches the firewall config.

## Memory-leak / unbounded-growth audit

This device is meant to run for months unattended. Several in-memory
structures grew forever with no eviction — each bounded only by how long
the process had been up, not by anything meaningful:

| Structure | Was | Now |
|---|---|---|
| `jam.devices` (every phone/kiosk that's ever joined) | Grew forever, never pruned | `Jam.prune_stale_devices()` drops entries not seen in 48h; bans (a separate set) are untouched by this |
| `admin_sessions` / `guest_sessions` (every login ever) | Plain `set()`, tokens never expired or removed | `SessionStore` with a TTL matching the cookie's `max_age` (30 days); lazy expiry on lookup **and** proactive hourly pruning |
| Rate-limiter bookkeeping (new in this pass) | N/A | Built with its own `prune()` from day one, so it doesn't become the next leak |
| pychromecast discovery browser | Never explicitly stopped on shutdown | `CastController.stop_discovery()` added, called from the app's shutdown path |

All four are pruned together from a single hourly sweep
(`_prune_long_lived_state()` in `main.py`, driven off the existing
10-second heartbeat ticker so no new background task was needed). None
of this is user-visible — an active session or a recently-seen device is
never touched; only things stale enough to be meaningless are dropped.

**Why this matters in practice:** a busy office running this for a year
could plausibly see thousands of distinct phones join over that time.
Without pruning, `jam.devices` and the session stores would hold all of
them forever, and `active_device_count()` additionally had to scan the
*entire* history on every single state request (every API call, every
10-second WebSocket heartbeat) to find the handful still actually
active — pure waste that grows without bound. Pruning fixes both the
memory growth and that scan cost together.

## Resource-utilization hardening (Raspberry Pi 4, 2GB)

- **systemd resource ceilings** added to `clinicjam-web`, `clinicjam-
  librespot`, and `clinicjam-portal-watch` (`MemoryHigh`/`MemoryMax`/
  `CPUQuota`) — generous headroom above each service's normal footprint,
  sized to stop a genuine runaway before it can starve hostapd/dnsmasq
  (which keep the Wi-Fi itself up) or the other services, rather than a
  tight budget that would fight normal operation. Verified with
  `systemd-analyze verify` that every directive parses correctly.
- **Sandboxing** (`NoNewPrivileges`, `ProtectSystem=strict`,
  `ProtectHome`, `PrivateTmp`, explicit `ReadWritePaths`) added to the
  same three services — standard, low-risk systemd hardening for
  processes that only ever need to read their own install + config and
  read/write their own state/runtime directories.
- **ffmpeg thread count capped** (`-threads 2` demux, `-threads 1`
  encode) — one MP3 encode at 320kbps needs nowhere near a full core;
  uncapped, ffmpeg will happily grab threads across all 4 cores and
  contend with the web server, librespot, and (if installed) the kiosk's
  Chromium for CPU.
- **journald capped** at 200MB total / 20MB per file
  (`/etc/systemd/journald.conf.d/clinicjam.conf`) — journald's out-of-
  the-box sizing assumes a desktop with much more free disk than a Pi's
  SD card, and this device runs six-plus always-on services logging
  continuously for months.
- **librespot's on-disk cache was already capped** at 1GB
  (`--cache-size-limit 1G`, set from the start) — confirmed still in
  place, no change needed.
- Confirmed no other unbounded-growth patterns in the audio pipeline:
  the `Broadcaster` in `stream.py` correctly removes a subscriber's
  queue on disconnect (`try/finally`), and crashed `ffmpeg`/fifo file
  descriptors are cleaned up in every exit path, not just the happy one.

## Scope notes (not fixed, and why)

- **No TLS.** This stays plain HTTP by design — it's a captive-portal
  kiosk server with no internet-routable exposure; adding a self-signed
  cert would trigger scary browser warnings on first contact, which is
  worse UX for zero realistic benefit on a network where the bigger
  mitigation (WPA2 on the Wi-Fi itself, see #5) is already addressed.
  If you ever expose this beyond the LAN, revisit this.
- **Admin bans list isn't auto-pruned.** Bans are an explicit admin
  action, bounded by how much an admin actually bans — not an automatic
  leak like the others above, so lower priority. Worth a "clear all
  bans" button in a future pass if it ever becomes unwieldy.
