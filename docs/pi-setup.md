# Setting up the Pi

From a blank SD card to a running board takes about half an hour on a Pi Zero, most of it waiting for the first boot and for one package to install. You need a PC with an SD card reader and both devices on the same Wi-Fi.

Tested on a Raspberry Pi Zero W Rev 1.1 with a Velleman VMP400 3.5" touchscreen. Other 3.5" SPI panels built on the ILI9486 display chip and the ADS7846 touch chip (Waveshare 3.5" A and its clones) should behave the same. They haven't been tried.

## 1. Flash the card

Use [Raspberry Pi Imager](https://www.raspberrypi.com/software/).

- **Device:** your Pi model.
- **OS:** Raspberry Pi OS (other), then **Raspberry Pi OS Lite (32-bit)** for a Zero or Zero W. There is no desktop to install: the app draws straight to the screen.
- **Settings** (the gear, or "Edit settings" when it asks):
  - hostname `overhead`
  - a user name and password
  - your Wi-Fi name and password
  - on the Services tab, enable SSH

**A Pi Zero W only has 2.4 GHz Wi-Fi.** If your router has separate names for its 2.4 and 5 GHz networks, give Imager the 2.4 GHz one. With the 5 GHz name the Pi boots and never appears on the network, and nothing tells you why.

Put the card in the Pi, fit the screen, and power it. The screen stays white: nothing drives it yet. The first boot takes 3 to 5 minutes on a Zero.

## 2. Copy the code over

From the PC, in the repo folder:

```bash
ssh pi@overhead.local "mkdir -p overhead"
```

```bash
scp -r overhead models pi tools tests main.py requirements.txt config.example.json pi@overhead.local:overhead/
```

Use your own user name in place of `pi`. If `overhead.local` isn't found, look up the Pi's address in your router's device list and use that.

Or install git on the Pi and clone the repo there. On a Zero that is slower than copying.

## 3. Run the installer

```bash
ssh -t pi@overhead.local "cd overhead && bash pi/install.sh"
```

It asks for your password once and prints each step. On a Zero the package install takes several minutes. Then:

```bash
ssh -t pi@overhead.local "sudo reboot"
```

After the reboot the board comes up in under a minute and asks for three taps to calibrate the touch panel. Use a fingernail or a stylus on each cross. If you miss, there's a redo in settings (Touch, Calibrate). Then a tips screen shows what tapping each part of the card does. Tap anywhere to put it away; it doesn't come back unless you ask for it in settings.

Until a location is set it shows demo traffic. Swipe up, tap Location, and enter your latitude and longitude. Two decimal places is enough: it puts you within about a kilometre, which is all the app sends to the data feeds anyway.

With a location set, the plane view downloads aerial photographs of your area once, 1 to 3 MB from USGS (EOX outside the US) and NASA, and bends them into its panorama in a background process at the lowest priority. Until that is done it shows drawn fields. To fetch no imagery, set `"ground_photos": false` in `config.json`.

## What the installer changes

Five things, all printed as it goes.

| Step | What | To undo |
|---|---|---|
| 1 | Installs `python3-pygame` with apt | `sudo apt remove python3-pygame` |
| 2 | Adds `dtparam=spi=on` and a display overlay line to `/boot/firmware/config.txt`. The old file is saved next to it as `config.txt.before-overhead`. | Copy the saved file back |
| 3 | Adds your user to the `video`, `input` and `netdev` groups, for the screen, the touch panel and Wi-Fi settings | `sudo gpasswd -d $USER video` and the same for the other two |
| 4 | Writes `/etc/systemd/system/overhead.service` and a small root-owned script, `/usr/local/lib/overhead/release-console.sh`, that stops the Linux text console drawing on the panel | `sudo systemctl disable --now overhead`, then delete both files |
| 5 | Adds `/etc/sudoers.d/020_overhead`, which lets your user run five exact commands without a password: `systemctl poweroff`, `systemctl reboot`, and `systemctl restart`, `stop` and `start` for `overhead.service` | `sudo rm /etc/sudoers.d/020_overhead` |

Step 5 is what makes the Shut down button work and lets `tools/pi.py deploy` restart the app.

**The app runs as your own user, not a locked-down one.** On an image made with Raspberry Pi Imager that user can usually run anything with `sudo` once it has your password, and on some images without one. So treat the Pi as a single-purpose device on a network you trust. [SECURITY.md](../SECURITY.md) lists what that does and doesn't protect against.

## The display overlay

The installer uses one of two overlays for the screen:

- **`piscreen`**, which ships with Raspberry Pi OS. Used by default. Not tested by this project on the VMP400: it drives the same two chips and should work, but "should" is all that can be said.
- **`tft35a`**, from the panel vendors' LCD-show driver package. This is what the tested setup runs. It comes with no license, so it isn't in this repo. If you have it (it is on the SD card image some of these screens ship with, in `/boot/overlays/`), copy `tft35a.dtbo` into the `pi/` folder before running the installer and it is used.

If the screen stays white after the reboot, or the colours or orientation are wrong, this line in `/boot/firmware/config.txt` is the place to look. `rotate=90` or `rotate=270` picks which way up landscape is.

## A faster screen

The screen is on SPI at 16 MHz, which carries about 2,080 lines a second. One aircraft replacing another repaints 258 lines, so that change shows at about 8 updates a second however fast the Pi draws it. Small changes (a distance ticking over) are already smooth.

The clock can go higher. How high depends on the panel and how it is connected.

```bash
bash pi/set-display.sh 32000000 60
```

```bash
sudo reboot
```

The first number is the SPI clock in Hz, the second how many times a second the driver sends changes. 32 MHz doubles the lines per second. The app reads the speed at start and paces itself to match.

If the picture comes back wrong (noise, shifted colours, a white screen), run it again with a lower number. `16000000` is the safe one. The script saves the previous `config.txt` as `config.txt.before-set-display`.

## Looking after it

From the PC, `tools/pi.py` does the routine things. Tell it where the Pi is once per terminal:

```bash
export OVERHEAD_PI=pi@overhead.local
```

| Command | What it does |
|---|---|
| `python tools/pi.py deploy` | Copies the code to the Pi and restarts the app. Never copies `config.json`. |
| `python tools/pi.py screen out.png` | Saves what the panel is showing right now |
| `python tools/pi.py logs` | The app's recent log lines |
| `python tools/pi.py status` | Whether it is running, uptime, memory |
| `python tools/pi.py test` | Runs the tests on the Pi |
| `python tools/pi.py bench` | Stops the app, times the heavy paths, starts it again |

A picture from `screen` shows live traffic and the time, which is enough to work out roughly where you are. It is saved as `pi-screen.png` by default, which git ignores. Don't post one. It will also show a Wi-Fi password if the keyboard is open.

On the Pi itself:

```bash
journalctl -u overhead -n 50
```

```bash
sudo systemctl restart overhead
```

To see how fast it is drawing, set `"stats": true` in `config.json` on the Pi and restart. Every 5 seconds the log gets a line with frames per second, drawing time, and lines sent to the screen.

## When something is wrong

| What you see | Likely cause |
|---|---|
| White screen after the reboot | The overlay didn't load. Check the `dtoverlay=` line in `/boot/firmware/config.txt` and `dmesg \| grep -i fb`. |
| Picture upside down | Change `rotate=90` to `rotate=270` in that line and reboot |
| Taps land in the wrong place | Settings, Touch, Calibrate. Use a stylus. |
| Touch does nothing | `journalctl -u overhead` should say `touch on /dev/input/event0`. If it doesn't, the user isn't in the `input` group yet: reboot. |
| `NO DATA`, red lamp blinking | No internet, or both aircraft feeds are down. Check Wi-Fi in settings. |
| Blank card, steady red lamp | Nothing in range right now. Widen the range or change Show to Everything to check. |
| The Pi never appears on the network | Wrong Wi-Fi name or password in Imager, or a 5 GHz network on a Zero W |
| Wi-Fi page says `NOT ALLOWED` | The user isn't in the `netdev` group yet: reboot |
