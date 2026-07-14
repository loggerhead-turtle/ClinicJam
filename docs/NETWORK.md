# The network: walled garden + captive portal

## Design goals

1. **Never touch the company network.** The Pi's uplink is its own
   (cellular modem or a dedicated line). Nothing about this system asks
   IT to share anything.
2. **Guests get the jam, not the internet.** Phones that join the jam
   Wi-Fi can reach exactly one thing: the jam server on the Pi.
3. **Speakers get local music + a trickle.** The Nests receive audio
   locally from the Pi. They're allowlisted (by MAC) for internet through
   the Pi's own uplink because Google devices refuse to behave with zero
   connectivity — but the music never streams over the uplink twice.

## Pieces

| Component | Job |
|---|---|
| `hostapd` | Runs the "ClinicJam" access point on the Pi's built-in radio (`wlan0`) |
| `dnsmasq` | DHCP + DNS for the jam subnet (default `10.24.7.0/24`); advertises the RFC 8910 captive-portal URL (DHCP option 114) |
| `nftables` | The walled garden (below) |
| NetworkManager | Left in charge of the uplink (modem/Ethernet) only; told to ignore `wlan0` |

All of it is rendered from `/etc/clinicjam/config.toml` by
`sudo clinicjam-apply` — edit the TOML, run that, done.

## The firewall, in plain words

- **Forwarding from the jam Wi-Fi is dropped** — except for source MACs
  in `[internet_allowlist]` (your Nest speakers), which are NATed out the
  uplink. A guest phone cannot reach the internet, the uplink, or
  anything else beyond the Pi.
- **Captive portal**: any HTTP request a guest makes to anywhere is
  DNAT'd to the jam app. The OS connectivity probes phones fire when they
  join Wi-Fi (`generate_204`, `hotspot-detect.html`, …) get a redirect,
  which makes iOS/Android pop the "sign in to network" sheet — showing
  the jam. HTTPS to the outside simply times out (you can't and shouldn't
  intercept it); the phones' probe flow doesn't need it.
- Client-to-client traffic on the AP is left enabled (`ap_isolate=0`)
  because the Google Home app needs phone↔speaker contact during
  one-time speaker setup. The garden wall is the forwarding rules, not
  client isolation.

## What guests experience

1. Join the **ClinicJam** Wi-Fi (open network by default; set
   `wpa2_passphrase` in config.toml if you want a password).
2. Their phone says "this network has no internet / sign in" and opens
   the jam app. They stay connected when asked ("Use this network as is"
   on Android; iOS keeps the portal sheet open).
3. Everything happens at `http://10.24.7.1/` (or `http://clinicjam.lan/`).

## Changing the allowlist

```sh
sudo nano /etc/clinicjam/config.toml    # add MACs under [internet_allowlist]
sudo clinicjam-apply                    # re-renders + reloads the firewall
```

## Verifying the wall

From a guest phone: the jam app loads, but any website should fail.
From the Pi:

```sh
sudo nft list table inet clinicjam      # see the drop rules + allowlist
sudo journalctl -u clinicjam-dnsmasq -f # watch leases as devices join
```

## Alternative uplink: joining another Wi-Fi network (e.g. the office guest network)

By default the Pi's own internet (for Spotify, speaker check-in, and OS
updates) comes from a cellular modem or Ethernet — see `docs/HARDWARE.md`.
If you'd rather not run a cellular plan, the Pi can instead join an
**existing Wi-Fi network as a client** — for example, the company's own
guest Wi-Fi — for its uplink. This is a different thing from the jam
Wi-Fi guests join, and the two never touch:

```
   company guest Wi-Fi                    "ClinicJam" Wi-Fi (unchanged)
   (their AP, their internet)             (the Pi's AP, zero internet)
        │                                        │
   USB Wi-Fi dongle (wlan1) ── Raspberry Pi ── built-in radio (wlan0)
        joins as a client        │              broadcasts the jam AP
                                  ▼
                    Spotify, speaker check-in, OS updates
```

This needs a **second radio** — a USB Wi-Fi dongle (~$10–15) — because the
Pi's built-in radio is already fully committed to hosting the jam AP; one
radio can't be an access point and a Wi-Fi client at the same time. It's
the same as any employee's laptop joining the guest network: the Pi only
consumes that connection for itself, it never re-shares it — guests on
the jam Wi-Fi still get zero internet, exactly as before.

To set it up:

```toml
# /etc/clinicjam/config.toml
[uplink]
mode = "wifi_client"

[uplink.wifi_client]
interface = "wlan1"        # the USB dongle — must differ from ap_interface
ssid = "CompanyGuest"
psk = ""                   # leave blank for an open network
```

```sh
sudo /opt/clinicjam/install/setup-uplink.sh
```

This creates a NetworkManager connection profile for the dongle, so it
reconnects automatically on boot or after a Wi-Fi drop — no different
from how your laptop remembers a network.

## Automating a captive-portal uplink

Many office guest networks show a "click to accept" splash page before
granting real internet. Since nothing is watching the screen on a
headless Pi, that would otherwise leave the uplink stuck forever. A
small watcher (`clinicjam-portal-watch`) polls for that stuck state and
replays the accept step automatically — you write the site-specific part
once, and it runs unattended after that.

**This only affects the Pi's own uplink.** It's unrelated to the
intentional captive portal guests see on the jam Wi-Fi.

Enable it in config.toml:

```toml
[uplink.captive_portal]
enabled = true
check_interval_seconds = 120
probe_url = "http://connectivitycheck.gstatic.com/generate_204"
```

Then capture what your specific portal actually does — this is the one
part that can't be shipped generically, since every vendor's portal is
different:

1. On a laptop, rejoin the guest Wi-Fi fresh so the portal triggers.
2. Open DevTools → Network tab → check "Preserve log".
3. Click through the portal's "I agree"/"Connect" button as normal.
4. Find the request that fired at that instant (usually a GET with a
   session token in the URL, or a POST with a field like `agree=true`).
   Right-click it → Copy → **Copy as cURL**.
5. Turn that into your click script:

   ```sh
   cp /opt/clinicjam/docs/portal_click.example.py /etc/clinicjam/portal_click.py
   nano /etc/clinicjam/portal_click.py   # paste your captured request in
   sudo systemctl restart clinicjam-portal-watch
   ```

`docs/portal_click.example.py` includes three ready-to-edit patterns:
a plain `urllib` GET/POST, a near-verbatim pasted `curl` command (usually
the fastest to get working), and a headless-browser click for portals
whose button runs JavaScript instead of a plain link. Delete whichever
you don't use.

Check that it's working:

```sh
sudo journalctl -u clinicjam-portal-watch -f
```

A quick heads-up worth giving whoever manages that guest Wi-Fi: this sets
up an unattended device that auto-accepts their network's terms on its
own, indefinitely. It doesn't share that connection with anyone else —
the Pi uses it exactly like one more employee laptop — but it's a
reasonable thing to mention rather than do silently.
