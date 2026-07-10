# Hardware

## Shopping list

| Item | Notes |
|---|---|
| Raspberry Pi 4 (2 GB+) or Pi 5 | The built-in radio covers a waiting-room-sized area and ~15–20 phones comfortably |
| Official USB-C power supply | Undervoltage causes flaky Wi-Fi — don't skimp here |
| microSD card, 16 GB+ (A1/A2 class) | For Raspberry Pi OS Bookworm 64-bit |
| USB cellular modem + data SIM | e.g. a USB 4G dongle with a Mint Mobile SIM. This is the Pi's ONLY internet. Steady-state usage is just Spotify audio for one stream (~150 MB/hr at 320 kbps) plus a trickle from the speakers |
| 5× Google Nest Audio | Already owned — they join the Pi's Wi-Fi |

Optional:

- **Ethernet cable** — if you'd rather uplink through a wall jack than
  cellular, plug it in; the firewall behaves identically (guests still get
  nothing).
- **Case with airflow** — the Pi runs a Wi-Fi AP, an encoder, and a web
  server; passive cooling is fine but give it air.

## Placement

Everything — the guests' phones and all five Nests — must be in range of
the Pi's radio. Put the Pi centrally (top of a cabinet beats inside one).
The Nests re-associate on their own after power cuts; the Pi's services
all auto-start on boot, so the whole system recovers from a power outage
unattended.

## The cellular modem

Most USB LTE dongles appear as `usb0` (RNDIS/ECM) or `wwan0` and are
handled automatically by NetworkManager on Raspberry Pi OS Bookworm.
Check with:

```sh
nmcli device            # should show the modem as 'connected'
ip route show default   # default route should point at the modem
```

`[uplink] interfaces` in `/etc/clinicjam/config.toml` is documentation of
intent — the firewall NATs allowlisted traffic out whatever non-AP
interface holds the default route, so a modem, Ethernet, or a USB Wi-Fi
dongle all work without config changes.
