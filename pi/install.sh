#!/bin/bash
# Sets up Raspberry Pi OS Lite for Overhead on a 3.5" SPI touchscreen (Velleman VMP400 and
# other ILI9486 + ADS7846 panels). Run it on the Pi, from the repo folder:
#
#   bash pi/install.sh
#
# It changes five things and prints each one. sudo asks for your password once.
# docs/pi-setup.md explains every step and how to undo it.
set -eu

DIR="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="$(id -un)"
CONFIG=/boot/firmware/config.txt
[ -f "$CONFIG" ] || CONFIG=/boot/config.txt

# The user name and the folder go into a systemd unit and a sudoers rule, so refuse
# anything that isn't a plain name or path.
case "$USER_NAME" in
  root) echo "Run this as your normal user, not root."; exit 1 ;;
  *[!a-z0-9_-]*|"") echo "Unexpected user name: $USER_NAME"; exit 1 ;;
esac
case "$DIR" in
  *[!A-Za-z0-9_./-]*) echo "Move the repo to a path without spaces or symbols: $DIR"; exit 1 ;;
esac

echo "1/5  Installing pygame"
sudo apt-get update
sudo apt-get install -y python3-pygame

echo "2/5  Enabling the display and touch panel in $CONFIG"
# If you have the panel vendor's overlay (tft35a.dtbo from the LCD-show driver package),
# put it in pi/ first and it is used. Otherwise the kernel's own overlay for the same
# chips is used. See docs/pi-setup.md for which one is tested.
OVERLAYS="$(dirname "$CONFIG")/overlays"
if [ -f "$DIR/pi/tft35a.dtbo" ]; then
  sudo install -m 644 -o root -g root "$DIR/pi/tft35a.dtbo" "$OVERLAYS/tft35a.dtbo"
  LINE="dtoverlay=tft35a:rotate=90"
else
  LINE="dtoverlay=piscreen,speed=16000000,rotate=90"
fi
if grep -q "^dtoverlay=tft35a\|^dtoverlay=piscreen" "$CONFIG"; then
  echo "     a display overlay is already there, left alone"
else
  sudo cp "$CONFIG" "$CONFIG.before-overhead"
  printf '\n# Overhead: 3.5 inch SPI touchscreen\ndtparam=spi=on\n%s\n' "$LINE" \
    | sudo tee -a "$CONFIG" >/dev/null
  echo "     added $LINE (the old file is saved as $CONFIG.before-overhead)"
fi

echo "3/5  Letting $USER_NAME use the screen, the touch panel and Wi-Fi settings"
sudo usermod -aG video,input,netdev "$USER_NAME"

echo "4/5  Starting Overhead on boot"
# A root-owned helper that stops the Linux text console (login prompt, blinking cursor)
# from drawing on the panel underneath the app. It lives outside the home folder so that
# only root can change what it does.
sudo mkdir -p /usr/local/lib/overhead
sudo tee /usr/local/lib/overhead/release-console.sh >/dev/null <<'EOF'
#!/bin/sh
for f in /sys/class/vtconsole/vtcon*; do
  if grep -q "frame buffer" "$f/name" 2>/dev/null; then echo 0 > "$f/bind"; fi
done
exit 0
EOF
sudo chmod 755 /usr/local/lib/overhead/release-console.sh
sudo tee /etc/systemd/system/overhead.service >/dev/null <<EOF
[Unit]
Description=Overhead flap card
After=network.target

[Service]
User=$USER_NAME
WorkingDirectory=$DIR
ExecStartPre=+/usr/local/lib/overhead/release-console.sh
ExecStart=/usr/bin/python3 -u -m overhead --fb
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable overhead.service

echo "5/5  Allowing five commands without a password: shut down, reboot, and restart, stop or start the app"
# Shut down is the button in the app's settings. Restart, stop and start are what
# tools/pi.py uses to deploy and to run the benchmark. Nothing else is opened up.
# The rule is written by root to a name sudo ignores (it has a dot in it), checked by
# visudo, then renamed into place. It never sits in a file another process could change,
# and a bad rule can never take effect.
RULE="$USER_NAME ALL=(root) NOPASSWD: /usr/bin/systemctl poweroff, /usr/bin/systemctl reboot, /usr/bin/systemctl restart overhead.service, /usr/bin/systemctl stop overhead.service, /usr/bin/systemctl start overhead.service"
NEW=/etc/sudoers.d/.020_overhead.new
printf '%s\n' "$RULE" | sudo tee "$NEW" >/dev/null
sudo chown root:root "$NEW"
sudo chmod 440 "$NEW"
if sudo visudo -cf "$NEW" >/dev/null; then
  sudo mv "$NEW" /etc/sudoers.d/020_overhead
else
  sudo rm -f "$NEW"
  echo "     skipped: the rule did not pass visudo's check"
fi

echo
echo "Done. Reboot to bring the screen up:   sudo reboot"
echo "After the reboot it asks for three taps to calibrate the touch panel."
echo "If something is wrong:   journalctl -u overhead -n 50"
