#!/bin/bash
# Change how fast the Pi talks to the SPI screen. Faster means smoother animation, up to
# the point where the panel can't keep up and the picture breaks into noise or colours
# go wrong. Run on the Pi:
#
#   bash pi/set-display.sh 32000000        # SPI clock in Hz. The installer sets 16000000.
#   bash pi/set-display.sh 32000000 60     # also how often the driver sends changes (default 30)
#   sudo reboot
#
# If the picture is wrong after the reboot, run it again with a lower number. 16000000 is
# the safe value. The app reads the speed at start and paces its drawing to match.
set -eu

SPEED="${1:-}"
FPS="${2:-}"
case "$SPEED" in ""|*[!0-9]*) echo "usage: bash pi/set-display.sh SPEED_HZ [FPS]"; exit 1 ;; esac
case "$FPS" in *[!0-9]*) echo "FPS should be a number"; exit 1 ;; esac
if [ "$SPEED" -lt 4000000 ] || [ "$SPEED" -gt 80000000 ]; then
  echo "SPEED_HZ should be between 4000000 and 80000000"; exit 1
fi

CONFIG=/boot/firmware/config.txt
[ -f "$CONFIG" ] || CONFIG=/boot/config.txt
LINE="$(grep -m1 '^dtoverlay=\(tft35a\|piscreen\)' "$CONFIG" || true)"
if [ -z "$LINE" ]; then
  echo "No tft35a or piscreen overlay line in $CONFIG. Run pi/install.sh first."; exit 1
fi

# drop any speed= and fps= already there, then add the new ones
NEW="$(printf '%s' "$LINE" | sed -e 's/,speed=[0-9]*//g' -e 's/,fps=[0-9]*//g' -e 's/:speed=[0-9]*,/:/' -e 's/:fps=[0-9]*,/:/')"
NEW="$NEW,speed=$SPEED"
[ -n "$FPS" ] && NEW="$NEW,fps=$FPS"

echo "was: $LINE"
echo "now: $NEW"
sudo cp "$CONFIG" "$CONFIG.before-set-display"
# Written by root next to the real file, then renamed over it.
awk -v old="$LINE" -v new="$NEW" '$0 == old && !done { print new; done = 1; next } { print }' "$CONFIG" \
  | sudo tee "$CONFIG.new" >/dev/null
if ! sudo grep -qxF -- "$NEW" "$CONFIG.new"; then
  sudo rm -f "$CONFIG.new"
  echo "Could not write the new line. Nothing was changed."; exit 1
fi
sudo mv "$CONFIG.new" "$CONFIG"
echo "Saved (the old file is $CONFIG.before-set-display). Reboot to apply:  sudo reboot"
