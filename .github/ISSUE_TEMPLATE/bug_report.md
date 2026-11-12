---
name: Bug report
about: The card, the plane view, touch, Wi-Fi or the installer did something wrong
labels: bug
---

**What happened**
What you did, what you expected, and what it did instead. A photo of the screen helps a lot.

**Where**
- [ ] On the Pi
- [ ] In a window on a PC (`python -m overhead --demo`)

**Hardware and versions**
- Pi model: (e.g. Zero W, Zero 2 W, 3B)
- Screen: (e.g. Velleman VMP400, Waveshare 3.5" A)
- OS: (run `cat /etc/os-release | head -2`)
- Overhead: (`__version__` in `overhead/__init__.py`, or the commit)
- Python and pygame: (run `python3 -c "import sys, pygame; print(sys.version, pygame.version.ver)"`)

**Log**
The last lines of `journalctl -u overhead -n 50` on the Pi, or the terminal output on a PC.

<!--
Leave out your coordinates, address, Wi-Fi name and config.json. Nobody needs them to fix a bug.
A screenshot of live traffic with the time on it can be traced back to roughly where you are.
Use the demo (`--demo`) for pictures if the bug shows there too.
-->
