# ClinicJam — step-by-step setup (no Linux knowledge needed)

Total time: about 45 minutes, most of it waiting for downloads.

## What you need

| Item | Notes |
|---|---|
| Raspberry Pi 4 (2 GB or more) or Raspberry Pi 5 | Its built-in Wi-Fi creates the "ClinicJam" network |
| Official Raspberry Pi power supply | A weak phone charger causes random Wi-Fi dropouts |
| microSD card, 16 GB or larger | Class A1/A2 if you can |
| A way to write the card | A laptop with an SD slot or a USB card reader |
| Internet for the Pi — pick ONE | **Ethernet cable** from a wall jack (easiest), **or** your **USB Wi-Fi dongle** so the Pi can join the office/guest Wi-Fi, **or** a USB cellular modem |
| Your Google Nest speakers | They join the Pi's Wi-Fi, not the office Wi-Fi |
| A Spotify **Premium** account for the office | Guests never need their own |
| A phone | For the setup steps and for the Google Home app |

**About the Bluetooth dongle:** you don't need it. Nest speakers only accept
music over Wi-Fi (Google Cast). Bluetooth could at best feed one speaker with
no sync between rooms, so ClinicJam doesn't use it. Leave it in the drawer.

**About the Wi-Fi dongle:** the Pi's built-in radio is fully used by the jam
network it creates. If you want the Pi to get *its own* internet over Wi-Fi
(instead of an Ethernet cable), plug the dongle in — the Pi uses it to join
your office or guest Wi-Fi like any laptop would. The office network is
never shared with anyone on the jam Wi-Fi.

---

## Step 1 — Download the image

Go to the project's **Releases** page on GitHub and download the newest
file named like `clinicjam-2026.10.28-abc1234.img.xz` (about 700 MB):

> https://github.com/loggerhead-turtle/ClinicJam/releases/latest

You don't need to unzip it.

## Step 2 — Flash it onto the SD card

1. Install **Raspberry Pi Imager** from https://www.raspberrypi.com/software/ (Windows, Mac, Linux).
2. Put the microSD card in your computer.
3. Open Imager:
   - **Choose Device** → your Pi model (Pi 4 or Pi 5).
   - **Choose OS** → scroll to the bottom → **Use custom** → pick the `.img.xz` you downloaded.
   - **Choose Storage** → the SD card.
   - Click **Next**. When Imager asks *"Would you like to apply OS customisation settings?"* click **No**
     (ClinicJam has its own settings file; Imager's settings would fight it).
   - Confirm the warning that the card will be erased. Writing takes 5–10 minutes.

## Step 3 — Fill in the settings file

When Imager finishes, **take the card out and put it back in**. A small
drive called **bootfs** appears on your computer. Open it and open the file
**`clinicjam-settings.txt`** with Notepad (Windows) or TextEdit (Mac).

Change these lines (everything else can stay as it is):

```
jam_wifi_name = ClinicJam            ← what people will see in their Wi-Fi list
jam_wifi_password = change-me-please ← 8+ characters. Phones AND speakers use it.
admin_pin = 246813                   ← YOUR host PIN, 6+ digits
guest_pin = 1357                     ← the PIN guests type to join (or leave empty)
```

If the Pi will get its internet from a **Wi-Fi dongle** instead of an
Ethernet cable, also fill in:

```
office_wifi_name = Clinic-Guest      ← the office / guest Wi-Fi the Pi should join
office_wifi_password = itspassword
```

Save the file (keep the name!), **eject** the card properly, and take it out.

> Changed your mind later? Put the card back in a computer, edit the file,
> boot the Pi again. It re-reads the file at every boot and writes a
> `clinicjam-status.txt` next to it telling you what it did.

## Step 4 — First boot

1. Put the card in the Pi.
2. Plug in the Ethernet cable **or** the Wi-Fi dongle (if you filled in the office Wi-Fi).
3. Plug in the power. Wait **3 minutes** — the first boot resizes the card and restarts itself once.
4. On your phone, open the Wi-Fi list. A network with the name you chose
   (e.g. **ClinicJam**) appears. Join it with the password you chose.
5. Your phone will say *"this network has no internet"* and most phones pop
   open the jam page by themselves. If not, open a browser and go to
   **http://10.24.7.1**

You should see the ClinicJam guest page. 🎉 It will say the jam hasn't started yet.

## Step 5 — Connect Spotify (host panel)

1. On your phone (still on the jam Wi-Fi), go to **http://10.24.7.1/admin**
   and enter your **admin PIN**.
2. Tap **Turn on setup mode (30 min)**. For the next 30 minutes the jam
   Wi-Fi has internet, which the next steps need. (Alternative: do steps 3–5
   over mobile data instead.)
3. Tap **Open Spotify login** and log in with the **office Spotify Premium**
   account. Approve the permissions.
4. Your browser lands on a page that **can't load** (address starts with
   `http://127.0.0.1:8898/login?code=…`). That is expected. **Copy the whole
   address** from the address bar.
5. Go back to the host panel, paste it in the box and tap **Finish**.

After ~20 seconds the status shows **Spotify account: connected** and
**Spotify player: online**. One paste links both; there is no "developer
app" to create.

## Step 6 — Put the Nest speakers on the jam Wi-Fi

Still in setup mode (you have 30 minutes; tap the button again if it ran out):

1. Open the **Google Home** app on your phone (phone on the jam Wi-Fi).
2. For each speaker: **+ → Set up device → New device** (or factory-reset an
   existing one: hold the mic-mute switch / the button on the back for ~15 s).
   When the app asks which Wi-Fi the speaker should use, pick the **jam Wi-Fi**
   and type its password.
3. When all speakers are set up: **+ → Create speaker group**, select all of
   them, name it e.g. **Office**.

Back in the host panel, under **Speakers**, tap ↻ and pick **🔊 group: Office**.
Set a starting volume with the slider.

The speakers show up in the **Internet allowlist** list automatically — that
is the small amount of internet they need to stay registered with Google.
Everything else on the jam Wi-Fi stays walled off once setup mode ends.

## Step 7 — Start the jam

Tap **▶ Start the jam**. Within a few seconds all speakers light up and play
whatever gets queued. With nothing queued, music comes from the **fallback
playlist** — paste any Spotify playlist link under **Jam rules → Save rules**
so there is never silence.

Put a sign at the front desk:

> Wi-Fi **ClinicJam** · password **…** · open **10.24.7.1** · jam PIN **1357**

---

## Everyday use

- **Guests:** join the Wi-Fi, enter their name + the jam PIN, search, tap **+**
  to queue, ▲ to upvote, **Vote to skip** when needed. No app, no account.
- **Host:** `http://10.24.7.1/admin` — pause, skip, remove songs, block a
  rowdy phone (blocks the phone itself, not just the browser), change the
  rules, pick speakers, volume.
- **Power cuts:** everything comes back on its own, including the speakers.
  The queue is cleared; the fallback playlist resumes.
- **Stop for the night:** ⏹ **Stop the jam** (or just leave it; the encoder
  only runs while the speakers are connected).

## Changing something later

| What | How |
|---|---|
| Wi-Fi name/password, PINs, office Wi-Fi | Edit `clinicjam-settings.txt` on the SD card, boot. |
| Spotify account | Host panel → **Disconnect account**, then connect again. |
| Jam rules, speakers, volume | Host panel. |
| Advanced settings | SSH in (set `login_password` in the settings file, user `admin`), edit `/etc/clinicjam/config.toml`, run `sudo clinicjam-apply`. |
| A touchscreen on the Pi itself | [docs/KIOSK.md](KIOSK.md) |

## Troubleshooting

| Symptom | What to check |
|---|---|
| No "ClinicJam" Wi-Fi after 3 minutes | Power LED on? Give it 2 more minutes. Then put the card in a computer and read `clinicjam-status.txt` on it — it lists any mistake in the settings file (e.g. password too short). |
| Phone joins but no page opens | Open a browser and type `10.24.7.1`. On iPhone, tap the **ClinicJam** network's ⓘ → **Use Without Internet** if offered. |
| Host panel says **Spotify player: not linked** after pasting | Paste the *whole* address including `?code=…`. If it says the link doesn't match, tap **Open Spotify login** again and paste the new one. |
| **Spotify player: linked, not visible** for more than a minute | The Pi has no internet. Ethernet plugged in? Office Wi-Fi name/password right (status file shows which network it tried)? Does that network need you to click "I agree" on a web page? See [docs/NETWORK.md](NETWORK.md#automating-a-captive-portal-uplink). |
| Google Home can't finish setting up a speaker | Make sure **setup mode** is on (host panel → Speakers). It turns itself off after 30 minutes. |
| Speaker group doesn't appear | Give it 30 s and tap ↻. All speakers powered and on the jam Wi-Fi? Group created in Google Home? |
| Only one speaker of the group plays | Set `client_isolation = no` in the settings file and reboot (see NETWORK.md for what that trades off). |
| Music stutters | Try `wifi_band = 5` in the settings file (speakers and phones both support 5 GHz), or move the Pi more centrally. |
| Forgot the admin PIN | It's in `clinicjam-settings.txt` on the card. |

## Keeping it updated

Every release on the GitHub page is a full image. To update, flash the new
image to the card and copy your `clinicjam-settings.txt` back onto it — the
Spotify link needs to be done again (two minutes). Or, over SSH:
`sudo /opt/clinicjam/install/install.sh` after pulling the new code.
