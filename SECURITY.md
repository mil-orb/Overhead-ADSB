# Security

Overhead runs unattended on a Pi on a home network. It reads four public data sources, lets you join Wi-Fi networks from its touchscreen, and its installer adds one narrow `sudo` rule. A bug in any of those matters.

## Reporting

Use [private vulnerability reporting](https://github.com/mil-orb/overhead-ADSB/security/advisories/new). Please don't open a public issue. Include the version (`overhead/__init__.py`), the Pi model and OS, and steps or data that show the problem. Leave your location and network name out.

You'll get a reply within 7 days.

## What counts

Anything that lets:

- a response from a data feed, or a Wi-Fi network name broadcast nearby, run code, write outside the app's `data/` folder, or crash the display in a loop
- the app's user become root through the installer's `sudo` rule, its systemd unit or its helper script
- the Wi-Fi password end up in a log, a file other than NetworkManager's own, or anywhere on screen after it is submitted
- the owner's exact coordinates leave the device (requests should carry them rounded to 0.01 degree)

## Known and accepted

- **The app runs as the user who installed it.** On a Raspberry Pi OS image made with Imager that user can normally use `sudo` for anything, so a bug that let a feed run code would own the Pi. The installer's own `sudo` rule is narrow (five exact `systemctl` commands), but it doesn't narrow what the user could already do. Running the app as a dedicated locked-down user is the fix, and it isn't done yet.

- **The Wi-Fi password is passed to `nmcli` as a command-line argument.** For the moment the command runs, another local user on the Pi could read it from the process list. The device is meant to be a single-user appliance.
- **Whoever can touch the screen can change settings**, join a Wi-Fi network and shut the Pi down. There is no lock.
- **Feeds are plain HTTPS to third parties.** They see the Pi's IP address and your rounded coordinates every few seconds.
- **The password field shows what you type** unless you press HIDE, because a resistive screen makes typing blind unreliable. A screen capture taken with `tools/pi.py screen` while the keyboard is open contains it.
- **A picture of the live screen locates you.** The flight, its distance and bearing, and the time are enough to work back to within about a kilometre using public flight history. Use `--demo` for pictures you share.

## Supported versions

Only the latest release gets fixes.
