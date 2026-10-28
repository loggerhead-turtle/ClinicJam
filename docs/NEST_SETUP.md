# Google Nest speaker setup

Goal: all the Nest speakers on the **jam Wi-Fi**, grouped into one Cast
**speaker group**, allowlisted for their trickle of internet. On the image
this is mostly automatic; here is what happens and what to do when it
doesn't.

## 1. Turn on setup mode

Host panel (`http://10.24.7.1/admin`) → **Speakers → Turn on setup mode
(30 min)**. While it's on, every device on the jam Wi-Fi has internet and
phones can talk to the speakers — both of which the Google Home app needs.
It turns itself off; tap again if you run out of time.

## 2. Put the speakers on the jam Wi-Fi

With your phone on the jam Wi-Fi, in the **Google Home** app:

1. **+ → Set up device → New device.** Factory-fresh speakers are found
   straight away. An already-set-up speaker: factory-reset it (Nest Audio:
   hold the mute switch area / the button on the back ~15 s until it says
   so) and it becomes "new".
2. When asked which Wi-Fi the speaker should use, pick the **jam SSID** and
   enter the jam Wi-Fi password.
3. Repeat for every speaker.

Each speaker appears in the host panel's **Internet allowlist** within a
minute of joining — that is its MAC being allowlisted automatically. (If
one doesn't, add its MAC by hand: Google Home → speaker → ⚙️ → Device
information → Wi-Fi MAC address, or read it from
`sudo journalctl -u clinicjam-dnsmasq`.)

## 3. Create the speaker group

Google Home: **+ → Create speaker group**, select all the Nests, name it
e.g. `Office`. The group is what makes all of them play in perfect sync —
the Pi casts one stream to the group and the speakers handle timing among
themselves.

## 4. Point the jam at the group

Host panel → **Speakers** → ↻ → choose `🔊 group: Office`. Set a starting
volume. Start the jam and all speakers should light up within a few seconds.

Setup mode can end now (it will by itself).

## Why the allowlist

The music is streamed to the speakers locally by the Pi, but Cast devices
misbehave without any internet at all (they want Google check-in and time
sync). The allowlist gives exactly those devices a narrow pipe — Google's
ports, public addresses only — out the Pi's own uplink. Guests remain fully
blocked, and the office network is never involved.

## Troubleshooting

| Symptom | Check |
|---|---|
| Google Home can't find / finish a speaker | Setup mode on? Phone on the jam Wi-Fi (not mobile data)? |
| Group doesn't appear in the host panel | All speakers powered + on the jam Wi-Fi? Give discovery ~30 s, tap ↻ |
| Speakers connect then drop after a while | Their MACs in the allowlist? `sudo clinicjam-fw status` |
| Only one speaker of the group plays | The station-to-station bridge isn't passing frames on your hardware: set `client_isolation = no` in the settings file and reboot (docs/NETWORK.md) |
| Audio stutters | Try `wifi_band = 5`, or another `channel` in config.toml, `sudo clinicjam-apply` |
| One speaker lags the others | Recreate the speaker group in Google Home; groups re-sync on creation |
| "Speakers: no target selected" after a reboot | Pick the group again under Speakers (it is remembered once chosen) |
