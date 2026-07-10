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
