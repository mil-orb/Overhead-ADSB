# Changelog

Notable changes, newest first. Versions follow `__version__` in `overhead/__init__.py`.

## 0.1.0 (2026-11-12)

First version. Runs on a Raspberry Pi Zero W with a Velleman VMP400 3.5" touchscreen.

- One aircraft per card on 40 x 74 px split flaps: flight, route, type, speed, altitude, distance, direction, elevation and a status flap. A seven-segment counter shows which aircraft this is out of how many.
- Visibility scoring from your horizon, the aircraft's angular size, haze, cloud layers and sun glare, with hysteresis. Positions are dead-reckoned between the 5-second data updates.
- Six show modes: in view, overhead, approaching, low flyers, military, everything. A range setting, 15 miles by default.
- Military and emergency aircraft get their own flap and lamp and interrupt the rotation.
- Flap animation from the equation of motion of a hinged plate, with two flaps in the air at once while a drum cycles.
- Plane view: tap the card for a 3D model posed as the aircraft looks from where you are. 36 models converted from Flightradar24's and amvlab's open-source sets, and generated shapes for 213 types in all.
- The plane view's sky follows the real one: colour by the sun's height, the sun and moon in their places (the moon in its phase), stars, cloud cover and haze from the weather feed, fields below the horizon with lights at night. The aircraft is lit to match and shows navigation lights after dark.
- A touch tips screen on first start, and again from settings, showing what each part of the card does.
- Settings sheet opened by a swipe up: Wi-Fi scan and join with an on-screen keyboard, location, brightness, night dimming, units, range, clock, horizon, seconds per aircraft, follow until gone, touch calibration, restart and shut down.
- Draws straight to the Linux framebuffer and reads the touch panel from evdev, so it needs no desktop. Three-point touch calibration.
- On a Pi Zero W a full card flip costs 21 ms of CPU a frame. Drawing is paced to what the SPI link can carry.
- Data from adsb.lol, airplanes.live, adsbdb.com and Open-Meteo. No keys. Coordinates are rounded to 0.01 degree in requests.
- Every network response is checked field by field before use, with size and time limits on the download. A stuck or failing tracker shows as NO DATA.
