# Hardware

## Shopping list

| Item | Notes |
|---|---|
| Raspberry Pi 4 (2 GB+) or Pi 5 | The built-in radio covers a waiting-room-sized area and ~15–20 phones comfortably |
| Official USB-C power supply | Undervoltage causes flaky Wi-Fi — don't skimp here |
| microSD card, 16 GB+ (A1/A2 class) | For Raspberry Pi OS Bookworm 64-bit |
| An uplink for the Pi — one of: an Ethernet jack; a USB Wi-Fi dongle (joins the office/guest Wi-Fi); a USB cellular modem + SIM | This is the Pi's ONLY internet. Steady-state usage is one Spotify stream (~150 MB/hr at 320 kbps) plus a trickle from the speakers |
| Google Nest speakers (any number) | Already owned — they join the Pi's Wi-Fi |

Not needed: a Bluetooth dongle. Cast speakers take music over Wi-Fi only,
and Bluetooth could not keep several rooms in sync.

Optional:

- **USB Wi-Fi dongle** — most common chipsets (Realtek 8188/8192/8821,
  MediaTek 7601/7610/7612, Ralink, Atheros 9271) have in-kernel drivers on
  Raspberry Pi OS and show up as `wlan1`. If `ip link` on the Pi doesn't
  list it, that dongle needs an out-of-tree driver — use Ethernet instead.
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
intent — the firewall NATs allowlisted traffic out whatever non-jam
interface holds the default route, so a modem, Ethernet, or a USB Wi-Fi
dongle all work without config changes. The image ships with ModemManager
disabled (`sudo systemctl enable --now ModemManager` for a modem that
needs it; most RNDIS/ECM dongles don't).
