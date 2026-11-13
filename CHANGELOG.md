# Changelog

Notable changes, newest first. Versions follow `__version__` in `overhead/__init__.py`.

## Unreleased

Faster on the Pi, with the same pictures.

- Writes to the panel happen on their own thread and no longer hold Python's lock while the SPI driver is busy. The main loop used to freeze for up to 168 ms on each one; in the same test on a Pi Zero the worst is now 5 ms. A push from the main loop went from 12 to 48 ms on average to 1.4 ms. A full flip still shows at about 8 updates a second at 16 MHz, which is the SPI link's limit. A full-flip frame costs about 4 ms more CPU for the extra copies.
- The plane view is no longer drawn in the background while it is closed. That took up to 1.5 s of the main loop at a time, about a fifth of every minute. It is drawn when you open it, quick while it slides in and smoothed when it stops.
- The cloud field is kept in `data/` after the first build instead of being made on every start (6 s on a Pi Zero, 1.7 MB on disk).
- Clouds are laid onto the sky in strips. One blit of the whole layer used to freeze the card for half a second on each repaint. A card frame during a sky repaint now takes 30 ms, worst 84 ms, down from 73 ms and 510 ms.
- The status lamps were redrawn twice a second when nothing had changed, which sent 520 lines a second to an idle panel. They now redraw only when they change.
- `--stats` also shows what reached the panel ("sent").

## 0.1.0 (2026-11-12)

First version. Runs on a Raspberry Pi Zero W with a Velleman VMP400 3.5" touchscreen.

- One aircraft per card on 40 x 74 px split flaps: flight, route, type, speed, altitude, distance, direction, elevation and a status flap. A seven-segment counter shows which aircraft this is out of how many.
- Visibility scoring from your horizon, the aircraft's angular size, haze, cloud layers and sun glare, with hysteresis. Positions are dead-reckoned between the 5-second data updates.
- Six show modes: in view, overhead, approaching, low flyers, military, everything. A range setting, 15 miles by default.
- Military and emergency aircraft get their own flap and lamp and interrupt the rotation.
- Flap animation from the equation of motion of a hinged plate, with two flaps in the air at once while a drum cycles.
- Plane view: tap the card for a 3D model posed as the aircraft looks from where you are. 36 models converted from Flightradar24's and amvlab's open-source sets, and generated shapes for 213 types in all.
- The plane view's sky follows the real one: colour by the sun's height, the sun and moon in their places (the moon in its phase), stars, low and high cloud generated to the weather feed's cover, and haze. The aircraft is lit to match and shows navigation lights after dark.
- Below the plane view's horizon, aerial photographs of the land around you (USGS in the US, Sentinel-2 cloudless by EOX elsewhere) as seen from 500 m up, hazed with distance and faded in winter, with NASA Black Marble street lights after dark. Fetched once per location, 1 to 3 MB, and built in a low-priority process.
- A touch tips screen on first start, and again from settings, showing what each part of the card does.
- Settings sheet opened by a swipe up: Wi-Fi scan and join with an on-screen keyboard, location, brightness, night dimming, units, range, clock, horizon, seconds per aircraft, follow until gone, touch calibration, restart and shut down.
- Draws straight to the Linux framebuffer and reads the touch panel from evdev, so it needs no desktop. Three-point touch calibration.
- On a Pi Zero W a full card flip costs 21 ms of CPU a frame. Drawing is paced to what the SPI link can carry.
- Data from adsb.lol, airplanes.live, adsbdb.com and Open-Meteo, imagery from USGS, EOX and NASA GIBS. No keys. Coordinates are rounded to 0.01 degree in requests, and map tiles are picked from that rounded position.
- Every network response is checked field by field before use, with size and time limits on the download. Map tiles are size-capped and their headers checked before any decoding. A stuck or failing tracker shows as NO DATA.
