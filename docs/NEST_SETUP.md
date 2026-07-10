# Google Nest speaker setup

Goal: all five Nest Audio speakers on the **jam Wi-Fi**, grouped into one
Chromecast **speaker group**, with their MACs allowlisted for internet
through the Pi's uplink.

Why the allowlist: the music is streamed to the speakers locally by the
Pi, but Cast devices misbehave without any internet at all (they want
Google check-in and time sync). The allowlist gives exactly those five
devices a small pipe out the Pi's own cellular/Ethernet uplink — still
nothing to do with the company network, and guests remain fully blocked.

## 1. Find each speaker's MAC address

If the speakers are already set up somewhere: Google Home app → speaker →
⚙️ Settings → **Device information** → Wi-Fi MAC address.

If they're factory-fresh, you can do this after step 3 instead — the
lease list on the Pi shows MACs as devices join:

```sh
cat /var/lib/misc/dnsmasq.leases 2>/dev/null; sudo journalctl -u clinicjam-dnsmasq | grep DHCPACK
```

## 2. Allowlist them

```sh
sudo nano /etc/clinicjam/config.toml
```

```toml
[internet_allowlist]
macs = [
  "aa:bb:cc:dd:ee:01",   # Reception
  "aa:bb:cc:dd:ee:02",   # Hallway
  "aa:bb:cc:dd:ee:03",   # Break room
  "aa:bb:cc:dd:ee:04",   # Office east
  "aa:bb:cc:dd:ee:05",   # Office west
]
```

```sh
sudo clinicjam-apply
```

## 3. Move the speakers onto the jam Wi-Fi

With your phone on the jam Wi-Fi (and your phone's MAC *temporarily*
added to the allowlist too — the Google Home app needs internet during
setup; remove it afterwards):

1. Factory-reset or "Set up new device" in the Google Home app.
2. When asked which Wi-Fi the speaker should use, pick the jam SSID.
3. Repeat for all five.

Tip: if the jam Wi-Fi is open (no password), Google Home handles that
fine; if you set a WPA2 passphrase in config.toml, have it ready.

## 4. Create the speaker group

In Google Home: **+ → Create speaker group**, select all five Nests, name
it something like `Office`. The group is what makes all five play in
perfect sync — the Pi casts one stream to the group and the speakers
handle timing among themselves.

## 5. Point the jam at the group

Admin panel → **Speakers** → refresh → choose `🔊 group: Office`.
Set a starting volume. Done — start the jam and all five speakers should
light up within a few seconds.

## Troubleshooting

| Symptom | Check |
|---|---|
| Group doesn't appear in the admin panel | All speakers powered + on the jam Wi-Fi? Give discovery ~30 s, hit refresh |
| Speakers connect then drop after a while | Their MACs really in the allowlist? `sudo nft list table inet clinicjam` |
| Audio stutters | Wi-Fi congestion — try another `channel` in config.toml, `sudo clinicjam-apply` |
| One speaker lags the others | Recreate the speaker group in Google Home; groups re-sync on creation |
