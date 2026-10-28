# ClinicJam security, memory and utilization audit

Date: 2026-10-28. Scope: everything in this repository as of the commit
before this audit — the FastAPI server (`server/clinicjam`), the guest/admin
front ends, the network configuration renderer, the installer, the systemd
units and the docs. Method: full read of every file by two independent
reviewers, plus exercising the HTTP layer with the test client to confirm
behaviour. Threat model: (1) a guest on the jam Wi-Fi, including a stranger
in radio range of an open network; (2) someone on the office network the
Pi's uplink joins; (3) someone at the touchscreen kiosk; (4) a hostile
captive portal or Spotify response.

Every finding below is marked **Fixed** (code in this commit), **Mitigated**
(risk reduced, residual noted) or **Documented** (inherent; called out to
the operator).

## Headline

The application code had no classic web bugs (no XSS sinks, no shell
injection, constant-time PIN compare, atomic state writes). The *system*,
however, did not deliver its central promise. The "walled garden" was only a
`forward`-chain filter: nothing restricted what a guest could reach *on the
Pi* (SSH, the admin API on port 8080, Spotify Connect), guests had full
recursive DNS through the uplink (a textbook tunnel out), station-to-station
Wi-Fi was open so anyone could cast straight to the Nests, the admin PIN was
4 digits with no rate limiting over plain HTTP on an open network, and the
Spotify player could never actually log in (librespot no longer accepts
password logins and nothing supplied a token). All of that is addressed.

## Critical / High

| # | Finding | Status |
|---|---|---|
| C1 | **No input firewall chain.** Every port on the Pi (SSH, the web app on 8080 bypassing the captive-portal DNAT, mDNS, Spotify Connect, the Cast discovery socket) was reachable from guests, and the web app bound `0.0.0.0` so the admin login was also reachable from the office-network side through the dongle. | **Fixed.** `render_network.py` now renders an `input` chain: on the jam interface only DHCP, DNS, HTTP/8080, ICMP and mDNS are accepted (SSH opt-in via `ssh_from_jam_wifi`); everything else is rejected. Ports 80/8080 are dropped on every other interface. The firewall unit is ordered *before* hostapd and validated with `nft -c` first. |
| C2 | **PIN brute force.** 4-digit numeric PINs, no rate limit or lockout, and the documented `curl \| sudo bash` install took the non-interactive branch and left the public default `2468`/`1357`. | **Fixed.** Per-address exponential lockout (30 s doubling to 15 min after 5 failures) plus a network-wide slowdown after 40 failures in 10 minutes (`auth.py`). Startup logs a loud warning for weak/default PINs; the admin panel shows it. Settings-file validation requires 6+ characters; the installer generates a random PIN when none is given. Non-ASCII PINs no longer 500. An empty admin PIN now disables admin login instead of granting it to everyone. |
| H1 | **DNS tunnelling.** Guests got full recursive DNS via the Pi's resolvers, i.e. a covert channel through the cellular plan or into the office resolver. | **Fixed.** Guest dnsmasq is a sinkhole (`no-resolv`, `address=/#/<pi>`), which also makes captive-portal detection more robust. A second dnsmasq on 10.24.7.2 is a real resolver reachable only by allowlisted speaker MACs (their port-53 traffic is DNATed there) or by everyone during setup mode. |
| H2 | **Spotify Connect hijack.** librespot advertised itself on the LAN; any guest could log *their* account into the office speakers via the Spotify app, bypassing queue, votes and the explicit filter, and knocking the host account offline. | **Fixed.** `--disable-discovery`; the player signs in with a token from the admin panel's one-time login (Spotify's desktop client id, PKCE), then with cached credentials. Discovery ports are also blocked by C1. |
| H3 | **Open station-to-station traffic.** The Cast protocol has no authentication; with `ap_isolate=0` any guest could cast, change volume, or ARP-spoof other guests. The `forward` chain never sees intra-BSS frames. | **Fixed (new default), switchable.** `ap_isolate=1` with the radio in a bridge (`br0`, hairpin) and an nftables `bridge`-family chain: speakers may talk to speakers (group sync needs it), nobody else talks to anybody. Setup mode opens it temporarily for the Google Home app. `client_isolation = false` restores the old behaviour if a driver misbehaves. |
| H4 | **MAC allowlist spoofable and over-broad.** A spoofed Nest MAC got unrestricted internet, including RFC 1918 hosts on the office side. | **Mitigated.** Egress for allowlisted MACs is limited to public addresses and Google's ports (tcp 80/443/5228–5230, udp 123/443). MAC spoofing itself cannot be prevented at L2; residual risk is a determined guest cloning a speaker's MAC to reach Google's ports — noted in NETWORK.md. |
| H5 | **Plaintext credentials over an open network; sessions never expired; no logout.** | **Mitigated.** Image and docs default to WPA2 (settings file asks for a password; open is allowed but warned). Sessions are HMAC-signed with expiry (admin 12 h, kiosk 15 min, guest 30 d), invalidated when the PIN changes, with `/api/logout` and `/api/admin/logout`. HTTP itself stays unencrypted (a captive-portal appliance cannot carry a trusted TLS certificate); the residual risk is a WPA2 peer with the handshake captured. |
| H6 | **Resource exhaustion on a 2 GB Pi.** Unbounded request bodies (a 1.5 GB POST OOM-killed the service), session sets and device dicts growing per request, unlimited `/stream.mp3` and WebSocket clients, 1024 file descriptors. | **Fixed.** 16 KB body cap (ASGI middleware, both content-length and chunked), bounded caches/limiters everywhere, devices capped at 500 and pruned, stream subscribers capped (default 6) and served only to speakers/host, WebSocket clients capped at 150 with 16 KB frames, uvicorn `limit_concurrency=200`, `LimitNOFILE=65536`, `MemoryMax=600M` so systemd restarts the service instead of the Pi swapping. |
| H7 | **Secrets world-readable** (`config.toml` with PINs/secrets, `state.json` with the refresh token, `hostapd.conf` with the WPA key, the PCM FIFO) — including by the kiosk user. | **Fixed.** `config.toml` 640 root:clinicjam, `/etc/clinicjam` and `/var/lib/clinicjam` 750, `hostapd.conf` 600, `nftables.conf` 640, `state.json` written 0600 with `UMask=0077` in the unit, FIFO 0600, librespot cache 700. |
| H8 | **The player could not log in at all.** librespot ≥0.5 removed password auth; the units supplied no credentials, so `/me/player/devices` would never list the Pi. | **Fixed.** PKCE login through the admin panel, token handed to a launcher that signs librespot in once; reusable credentials are cached afterwards. The same token serves the Web API, removing the need for a Spotify developer app (one remains optional). |

## Medium

| # | Finding | Status |
|---|---|---|
| M1 | `forward` policy `accept`, so with two uplinks (Ethernet + dongle) the Pi routed between office segments; firewall not ordered before hostapd; no validation of the ruleset. | **Fixed.** Policy drop, explicit accepts only; `Before=` ordering and `Requires=`; `nft -c` pre-check; `ip_forward` on but IPv6 forwarding explicitly off. |
| M2 | Spotify API amplification: unbounded search traffic, `Retry-After` honoured for hours blocking the player loop, `int()` crash on HTTP-date values. | **Fixed.** Per-IP limiter (40 searches/min), 10-minute search cache, track cache so adds don't hit the API, `Retry-After` capped at 5 s and parsed defensively, network errors mapped to 503. |
| M3 | Self-asserted `X-Device-Id`: listener count and skip threshold could be inflated by anyone without the PIN; bans evaded by clearing storage. | **Mitigated.** Only devices that joined with the PIN count as listeners; bans also record the device's MAC (from the neighbour table) and block the hardware, not just the browser. Device ids are sanitised. Full identity would need per-user accounts, which the product deliberately avoids. |
| M4 | WebSocket broadcast was sequential with no timeout: one stalled phone blocked updates for all, and broadcast tasks were created without references. | **Fixed.** Concurrent sends with a 3 s timeout, stalled clients dropped, one JSON render per device id, broadcasts coalesced (many votes in 50 ms → one broadcast), task references kept. |
| M5 | Supply chain: `curl \| sh` as root, `git pull` of a feature branch on re-run. | **Mitigated.** The raspotify apt repo is added with its signing key and installed through apt (signature-verified) instead of piping a script into `sh`; the image build pins the base OS image by SHA-256; re-running the installer no longer pulls code. Python requirements remain version-ranged (no hash lock) — acceptable for an offline image built from a reviewed commit. |
| M6 | Kiosk: the admin cookie lived 30 days in a persistent Chromium profile; "lock" was UI-only. | **Fixed.** Kiosk admin sessions last 15 minutes, re-locking calls logout, Chromium runs `--incognito`. |
| M7 | Captive-portal auto-click trusted whatever the (possibly spoofed) network served: form actions could point at the Pi or internal hosts; unbounded response read. | **Fixed.** Submits only to the portal's own host, refuses loopback/link-local targets, reads at most 256 KB; the watcher unit has `IPAddressDeny` for the jam subnet and no privileges. Empty-PSK uplinks are warned about. |
| M8 | pychromecast lifecycle: a new `Chromecast` object per discovery update, never disconnected → leaked socket threads over weeks of speaker power-cycles; `ensure_casting` tasks fire-and-forget. | **Fixed.** One object per uuid, reused on updates, disconnected on removal and shutdown; single in-flight cast check with a kept reference; dict copied before iteration. |
| M9 | `/api/internal/librespot-event` was callable by any local process (the kiosk browser) and 500'd on bad JSON. | **Fixed.** Per-boot bearer token shared via `/run/clinicjam/hook.token` (0640), pydantic-validated body. |

## Low

| # | Finding | Status |
|---|---|---|
| L1 | No CSRF/Origin defence beyond Starlette's implicit `SameSite=lax`. | **Fixed.** `samesite="lax"` is now explicit; state-changing routes take JSON bodies only; CSP `form-action 'self'`. |
| L2 | No security headers; two `innerHTML` uses (integers only). | **Fixed.** CSP, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`, `Cache-Control: no-store`; DOM APIs instead of `innerHTML`; API docs endpoints disabled. |
| L3 | `state.json` written without `fsync` (power loss could drop the Spotify link); silent fallback to the repo's example config. | **Fixed.** fsync file + directory, keep a `.bak`, load from backup on corruption. |
| L4 | Track/playlist ids spliced into API paths unvalidated (`..%2F` reached the URL). | **Fixed.** 22-char base62 validation for every id that comes from a guest or admin. |
| L5 | OAuth without `state`; installer PIN injected into TOML via `sed`. | **Fixed.** Random `state` verified on paste; settings are written through a TOML-aware editor with validation. |
| L6 | No systemd hardening on any unit. | **Fixed.** `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome`, `PrivateTmp/Devices`, empty capability sets, syscall filter, address-family limits, memory caps on the web, player, and watcher units. Root-only work moved to a path-triggered helper (`clinicjam-fw`) fed by a request queue, so the web service holds no privileges at all. |
| L7 | Album art pointed at Spotify's CDN, which guests cannot reach — broken images for every guest. | **Fixed.** `/api/art` proxy restricted to `i.scdn.co/image/<40 hex>` with a 12 MB LRU and a per-IP miss limiter. |
| L8 | Information disclosure: `/api/state` and `/ws` are public; dnsmasq lease file world-readable. | **Documented.** The queue and listener count are meant to be visible to anyone on the jam Wi-Fi (that is the product); the lease file moved to the private `/var/lib/clinicjam`. |

## Memory leaks (summary)

| Where | Problem | Fix |
|---|---|---|
| `main.py` session sets | +1 entry per login, never pruned | Stateless signed tokens; nothing stored |
| `jam.devices` | +1 per unique header value, never pruned; inflated vote maths | Capped at 500, pruned after 24 h, only joined devices counted |
| `cast.py` | New `Chromecast` (socket thread) per discovery update, never disconnected | One per uuid, disconnect on remove/stop |
| `player.py` / `main.py` | `create_task` results discarded (GC'd mid-flight, unobserved exceptions, overlapping runs) | References kept; single in-flight cast check; broadcast coalescing |
| `events.py` | Blocked `send_text` tasks accumulating behind a stalled socket | Timeout + drop + concurrent send |
| Login limiter / rate limiters / caches | (new) | All bounded with explicit caps |

## Server utilization

| Change | Effect on a Pi 4 |
|---|---|
| ffmpeg runs only while a client is pulling `/stream.mp3` (+30 s grace); the FIFO is always drained so librespot never blocks | ~6–8 % of a core and ~25 MB RSS saved whenever the jam is stopped or nobody is casting — i.e. nights and weekends |
| Search results cached 10 min, tracks cached 1 h, `find_device` cached 30 s for the admin poll | Fewer Spotify round-trips (the admin page alone was making one API call every 8 s) and faster adds |
| Broadcast coalescing + one JSON render per device | A burst of 20 votes is one broadcast instead of 20 |
| Heartbeat 10 s → 15 s, device pruning every minute | Less Wi-Fi airtime, flat memory |
| uvicorn: access log off, `proxy_headers` off, keep-alive 10 s, concurrency limit | Journal stays quiet (it lives in RAM on Pi OS), no amplification, graceful shedding instead of swap |
| Image: Bluetooth, ModemManager (re-enable for a USB modem), apt timers off; swap 256 MB; journald volatile | Faster boot, ~40 MB less RAM, less SD wear |
| 5 GHz band option for the jam AP | Optional: more headroom for audio in crowded 2.4 GHz offices |

## Residual risks the operator should know

- **Wi-Fi is the trust boundary.** WPA2 with a shared password means any
  guest who has the password and captures another device's handshake can
  decrypt that device's traffic, including the host entering the admin PIN
  over HTTP. Enter the admin PIN from your own phone, not a shared one, and
  change the Wi-Fi password when staff leave.
- **MAC spoofing.** A guest who clones a speaker's MAC gets the speaker's
  (narrow) internet allowance. The firewall limits that to Google's ports on
  public addresses.
- **Setup mode is intentionally open.** For up to 30 minutes everything on
  the jam Wi-Fi has internet and can reach the speakers. Use it only while
  you are setting up, in a quiet moment; it turns itself off.
- **SSH.** Off until `login_password` is set in the settings file; when on,
  it is reachable from the uplink side (office network) with that password.
  Use a strong one.
- **Spotify terms.** One Premium account plays music for a room; that is the
  same as a laptop plugged into speakers. Spotify's own client id is used for
  the login the same way librespot does; if Spotify ever blocks it, a
  developer app can be configured instead (SPOTIFY_SETUP.md).
