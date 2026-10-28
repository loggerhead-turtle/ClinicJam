# The network: walled garden + captive portal

## Design goals

1. **Never touch the company network.** The Pi's uplink is its own
   (Ethernet, cellular modem, or a dongle joined to the guest Wi-Fi like any
   laptop) and is never shared onward. Nothing on the Pi is reachable from
   that side either, except SSH if you enable it.
2. **Guests get the jam, not the internet.** Phones that join the jam
   Wi-Fi can reach exactly one thing: the jam app on the Pi. Not the
   internet, not DNS, not SSH, not each other, not the speakers.
3. **Speakers get local music + a trickle.** The Nests receive audio
   locally from the Pi. They're allowlisted (by MAC) for a narrow slice of
   internet through the Pi's own uplink — Google's ports, public addresses
   only — because Cast devices refuse to behave with zero connectivity.

## Pieces

| Component | Job |
|---|---|
| `hostapd` | Runs the jam access point on the Pi's built-in radio (`wlan0`), WPA2, `ap_isolate=1` |
| `br0` | A bridge the radio is enslaved to (hairpin on); gives the firewall a say over station-to-station traffic |
| `dnsmasq` ×2 | #1 on 10.24.7.1: DHCP + DNS **sinkhole** for guests (every name → the Pi, nothing forwarded). #2 on 10.24.7.2: a real resolver only speakers can reach |
| `nftables` | The walled garden (below); rendered to `/etc/clinicjam/nftables.conf` |
| `clinicjam-fw` | The one root helper: adds/removes allowlist MACs live, runs *setup mode*, driven by a request queue the (unprivileged) web app writes to |
| NetworkManager | Left in charge of the uplink only; told to ignore `wlan0` and `br0` |

All of it is rendered from `/etc/clinicjam/config.toml` by
`sudo clinicjam-apply` — edit the TOML (or the settings file on the SD
card), run that, done.

## The firewall, in plain words

**Into the Pi (input chain)** from the jam Wi-Fi: DHCP, DNS, HTTP (80 and
the app port), ICMP and mDNS are accepted; everything else is rejected —
including SSH unless `ssh_from_jam_wifi = true`. From the uplink side the
jam app's ports are dropped; the OS defaults (SSH) apply to the rest.

**Through the Pi (forward chain, policy drop)**: only source MACs in the
allowlist (your speakers) may open connections, and only to public
addresses on tcp 80/443/5228–5230 and udp 123/443. Guests get a TCP reset
or ICMP reject immediately (phones figure out "no internet" faster that
way). The Pi never routes between two uplinks.

**Between stations (bridge chain, policy drop)**: hostapd hands every
station-to-station frame up to the bridge, which re-sends it only if the
source is a speaker (a Cast group streams audio between its members). A
guest phone cannot reach a speaker (the Cast protocol has no password) or
another phone (no ARP games).

**Captive portal (nat prerouting)**: any guest HTTP request is DNATed to the
jam app. Because the guest DNS is a sinkhole, every hostname already points
at the Pi, and the OS connectivity probes (`generate_204`,
`hotspot-detect.html`, …) get a redirect, which pops the "sign in to
network" sheet showing the jam. HTTPS to the outside gets a reset.

**Speakers' DNS**: their port-53 traffic is DNATed to the real resolver on
10.24.7.2 (keyed on the same MAC set), so they resolve Google normally
while guests resolve nothing.

## Setup mode

The Google Home app needs the phone, the speaker *and* Google to see each
other while it sets a speaker up or creates a group. Host panel → **Turn on
setup mode** inserts, for 30 minutes, rules tagged `setup-mode` that: let
every station reach every other station, DNAT everyone's DNS to the real
resolver, skip the captive-portal DNAT, and forward everyone to public
addresses. A systemd timer removes them (and flushes conntrack) when time
is up; `clinicjam-fw setup-mode off` does it immediately.

## What guests experience

1. Join the **ClinicJam** Wi-Fi with the password on the sign.
2. Their phone says "this network has no internet / sign in" and opens
   the jam app. They stay connected when asked ("Use this network as is"
   on Android; "Use Without Internet" on iOS).
3. Everything happens at `http://10.24.7.1/`.

## The allowlist

Speakers found on the jam Wi-Fi (via Cast discovery) are added
automatically; the host panel shows the list and lets you add or remove a
MAC by hand. On disk it is `/etc/clinicjam/allowlist.json` plus whatever is
in `[internet_allowlist]` in config.toml. Changes are live (`nft add
element`) and survive reboots.

```sh
sudo clinicjam-fw status                 # what's allowed, setup mode state
sudo clinicjam-fw allow aa:bb:cc:dd:ee:ff "Reception"
sudo clinicjam-fw revoke aa:bb:cc:dd:ee:ff
sudo nft list table inet clinicjam       # the live ruleset
sudo journalctl -u clinicjam-dnsmasq -f  # leases as devices join
```

## `client_isolation = false`

If your Wi-Fi driver does not deliver station-to-station frames to the
bridge (symptom: only the group leader plays), set `client_isolation =
false` (settings file: `client_isolation = no`) and reboot. The radio then
bridges stations in firmware, which the firewall cannot see: phones can
reach the speakers and each other. The internet wall, the DNS sinkhole and
the input chain are unaffected.

## Uplink options

By default the Pi's own internet comes from Ethernet or a USB modem (both
are "just an interface with a default route" to NetworkManager; for a USB
cellular modem run `sudo systemctl enable --now ModemManager`, which the
image leaves off).

### Joining another Wi-Fi network with a dongle (`wifi_client`)

```
   office guest Wi-Fi                     "ClinicJam" Wi-Fi (unchanged)
   (their AP, their internet)             (the Pi's AP, zero internet)
        │                                        │
   USB Wi-Fi dongle (wlan1) ── Raspberry Pi ── built-in radio (wlan0)
        joins as a client        │              broadcasts the jam AP
                                  ▼
                    Spotify, speaker check-in, OS updates
```

This needs a **second radio** (a USB Wi-Fi dongle) because the built-in
radio is fully committed to hosting the jam AP. The Pi only consumes that
connection for itself; the firewall never forwards a guest there. On the
image, filling in `office_wifi_name` / `office_wifi_password` in the
settings file is all it takes. By hand:

```toml
[uplink]
mode = "wifi_client"
[uplink.wifi_client]
interface = "wlan1"
ssid = "CompanyGuest"
psk = "the-password"
```

```sh
sudo /opt/clinicjam/install/setup-uplink.sh
```

That writes a NetworkManager keyfile, so it reconnects on boot and after
drops. A network without a password is accepted but warned about: the Pi
would join *any* AP with that name (evil twin). 802.1X/enterprise networks
need a hand-written NetworkManager profile.

## Automating a captive-portal uplink

Many office guest networks show a "click to accept" page before granting
internet. A watcher (`clinicjam-portal-watch`) polls for that state and
replays the accept step. Enable it in config.toml:

```toml
[uplink.captive_portal]
enabled = true
check_interval_seconds = 120
probe_url = "http://connectivitycheck.gstatic.com/generate_204"
```

If the portal is a single "I agree" button, the automatic option usually
works unmodified:

```sh
sudo cp /opt/clinicjam/docs/portal_click.example.py /etc/clinicjam/portal_click.py
sudo systemctl restart clinicjam-portal-watch
sudo journalctl -u clinicjam-portal-watch -f
```

It follows the portal's redirect, then submits the form or follows the
most button-like link **on the portal's own host only** (it refuses to be
pointed at the Pi, the jam subnet or anything else). If that doesn't get
you online, capture your portal's request with your browser's DevTools
("Copy as cURL") and paste it into `click_via_curl()` as the example
explains.

Tell whoever manages that guest Wi-Fi: this is an unattended device that
auto-accepts their terms, uses the connection exactly like one more laptop,
and shares nothing with anyone.
