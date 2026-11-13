# AGENTS.md

Guidance for coding assistants (Claude Code, Codex, Copilot, Cursor and others) working in this repository. People will find it useful too.

Overhead is a split-flap display of the aircraft visible from one spot, running unattended on a Raspberry Pi Zero with a 3.5" SPI touchscreen. Python 3.11+ and pygame, nothing else. The same code runs in a window on a PC, which is where almost all work and testing happens.

## Safety first

Some things here act on a real device in someone's house, and some files hold where they live.

**Never:**

- Run `pi/install.sh` or `pi/set-display.sh` on the machine you're working on. They are for the Pi, and they edit boot config, systemd and sudoers.
- SSH to, scan for, or log in to any host other than the one the person gave you as `OVERHEAD_PI`. Other devices on their network are none of this project's business.
- Run anything with `sudo` on the Pi beyond `systemctl restart|stop|start overhead.service`, `systemctl reboot` and `systemctl poweroff`, which the installer allows without a password. Anything else that needs root is for the person to run. Never type or ask for a password.
- Commit `config.json`, `data/`, `shots/`, `pi-backup/` or `.models-src/`. `config.json` holds the owner's coordinates and Wi-Fi name. They are in `.gitignore`: don't force-add them, and don't paste their contents into code, tests, docs, commit messages or issues.
- Put a real location, flight log, network name or address in a test, a doc or an image. Use the demo traffic and made-up coordinates (the tests use 40.0, -100.0). A plane view at a real location shows aerial photographs of it.
- Download anything without asking first: packages, fonts, models, firmware, data sets. Say what, from where and how big.
- Add a model to `models/` without its source, license and credit in the file and in `models/README.md`. Never add one whose license doesn't allow redistribution.

**Always safe:**

- `python -m tests`, `python -m overhead --demo`, anything with `--screenshot`
- `python tools/media.py`, `python tools/model_sheet.py`, `python tools/sky_sheet.py`, `python tools/bench.py`
- `python tools/pi.py screen|logs|status` (read-only on the Pi). A `screen` picture shows live traffic and the time, which gives away roughly where the owner lives: never commit or post one.

`python tools/pi.py deploy` copies code to the person's Pi and restarts the app. Do it when they ask you to put a change on the device, not on your own.

## Commands

```bash
python -m tests                                   # all tests, about 10 s, no network or screen; must pass before you say you're done
python -m overhead --demo                         # run it in a 480x320 window with simulated traffic
python -m overhead --demo --screenshot out.png --at 0.4,12      # headless: out_0.png mid-flip, out_1.png settled
python -m overhead --demo --screenshot out.png --at 30 --plane  # the plane view
python -m overhead --demo --screenshot out.png --at 30 --plane --sky dusk   # under another sky: day, dusk, night, overcast
python -m overhead --demo --screenshot out.png --at 3 --settings main   # a settings page: main, show, wifi, key, loc
python -m overhead --demo --screenshot out.png --at 12 --hints          # the touch tips screen
python tools/media.py                             # rewrite docs/images/*.png
python tools/model_sheet.py out.png --small B738 C172 C17       # look at aircraft models
python tools/sky_sheet.py out.png                 # the plane view's sky and ground under five kinds of light
python tools/sky_sheet.py out.png --ground        # with aerial photographs of the made-up place (about 2 MB download the first time)
python tools/bench.py                             # time the heavy paths on this machine
python tools/pi.py deploy | screen out.png | logs | status | test | bench
```

You can't see a window, so look at your work with `--screenshot` and open the PNG. On the Pi, `tools/pi.py screen` copies what the panel is showing.

## Map

Read [docs/architecture.md](docs/architecture.md) for the full picture. The short version:

- `overhead/sky.py`: where an aircraft is in the observer's sky and whether a person could see it. Pure math, no I/O.
- `overhead/feeds.py`: the network sources (aircraft, routes, weather), the on-disk route cache, and the demo traffic. Its parsers are the only door for network data.
- `overhead/tracker.py`: turns aircraft into an ordered list for the card, on its own thread. The show modes live here.
- `overhead/board.py`: the split-flap renderer. `FaceSet` holds pre-composed pictures of every half-window mid-flip; `Module` is one drum.
- `overhead/hangar.py`: the plane view and its software 3D renderer. `overhead/models.py`: the aircraft meshes, generated or loaded from `models/`. `overhead/ground.py`: aerial photographs and night lights for the plane view's ground, fetched once per location and built in a low-priority child process. `overhead/scene.py`: the sky and ground behind the aircraft, painted on a thread from the sun, moon and weather.
- `overhead/settings.py`: the settings sheet. `overhead/hints.py`: the touch tips screen shown on first start. `overhead/wifi.py`: `nmcli`, or stand-in networks on a PC.
- `overhead/device.py`: the Pi's framebuffer and touch panel, with no desktop in between.
- `overhead/app.py`: config, input, the main loop.
- `pi/`: the installer. `tools/`: developer tools. `tests/`: plain scripts of assertions.

## Rules

These hold everywhere. A change that breaks one won't be merged.

1. **Standard library and pygame only.** No new dependency unless the person asks. Then: exact version, out at least 14 days, reason in the commit message.
2. **Everything from the network is untrusted.** Aircraft data enters through `parse_aircraft`, routes through `route_entry` and `clean_route`, weather through `Weather.refresh`. Each checks types, ranges and finiteness (JSON allows `NaN` and `1e999`) and cuts text to short printable strings. A bad record is dropped, never passed on, and never costs the records around it. The cache file on disk is checked the same way when it is read. Map tiles enter through `ground.read_tile`, which checks the size, the signature and a 256 x 256 header (`feeds.image_header`) before pygame decodes anything. Wi-Fi names from a scan are untrusted too: they reach `nmcli` only as separate arguments, never through a shell. If you add a field or a source, add its hostile cases to `tests/test_feeds.py`.
3. **Nothing blocks the main loop.** Network and tracking run on the tracker's thread. Wi-Fi commands run on a worker. The plane view's sky is painted on its own thread. Animation is driven by elapsed time, so a slow frame skips positions and keeps the right speed.
4. **Count the lines.** The screen receives about 2,080 lines a second over SPI. Redraw only what changed and return those rectangles. A change that repaints the whole screen every frame will run at 6 frames a second on the device, however it looks on your PC. State the cost of anything you add: `python tools/bench.py` prints it.
5. **No blending in the flap path.** Moving flaps are plain copies of pre-composed pictures in the screen's own pixel format. That is what took a full card flip from 80 ms a frame to 21 ms on a Pi Zero. Don't add a per-flap overlay, alpha or scale.
6. **Touch is for a small resistive screen in a case.** Targets at least 56 px tall or a third of the screen. No gesture that starts at an edge. No double taps. Every action has feedback.
7. **Flaps only show what a drum has.** Letters, digits and a few signs (`ALPHA` and `NUM` in `board.py`). Words are whole flaps on a word drum (`REMARKS`).
8. **Models keep their licenses.** Converted models stay in `models/` with their credit. Generated shapes are the fallback for every type and must always build.
9. **Say what you couldn't check.** Display speed, touch feel, `nmcli` and the installer can only be verified on a Pi. Name anything you changed that needs it.

## Recipes

**Add a field to the card.** Add a `_field` to the right line in `card_spec` in `board.py`, fill it in `App.card` in `app.py`, and check that the first line still fits: the layout solver sizes every flap from the width, and prints the flap width with `--screenshot`. Add it to the card assertions in `tests/test_ui.py` and re-run `tools/media.py`.

**Add a status word.** Append it to `REMARKS` in `board.py` (the drum order is the list order), give it a colour in `word_faces` if it isn't cream on black, and decide when it shows in `App.card`.

**Add a show mode.** Add a row to `MODES` and a branch to `wanted` in `tracker.py`. The name must fit the settings list (12 characters) and the description one line (64). `tests/test_sky.py` checks every mode against the demo traffic: add what your mode should and shouldn't include.

**Add a settings row.** Add a tuple to `rows` in `Settings._page_main`, add its key to `DEFAULTS` in `app.py` (or it won't be saved), and react to it in `App.after_settings` if it needs more than a redraw. Row indexes in `tests/test_ui.py` shift when you insert one: fix them.

**Change how the sky looks.** `scene.py`. `sky_colours` is the table of sky colours by sun height, `tone` the light on the aircraft, `Scene.paint` the panorama. Look at all five skies at once with `python tools/sky_sheet.py out.png`. `Scene.state` decides when a repaint is needed: if you add an input, add it there or the picture won't update. Painting takes about 2 seconds on a Pi Zero, on a thread, so keep per-pixel Python loops out of it. Clouds come from `cloud_field`, built once per run; `_clouds` turns the weather's cover into thresholds, and `tests/test_scene.py` checks that a cover fraction shows as about that much cloud high and low. The real ground is `ground.py`: look at it with `tools/sky_sheet.py --ground`.

**Change a visibility factor.** `visibility` in `sky.py`. Write the case in `tests/test_sky.py` first (the `score` helper takes wingspan, ground distance and altitude), watch it fail, then change the rule. Update the table in the README and [docs/visibility.md](docs/visibility.md).

**Add an aircraft type.** For a generated shape, add a row to `TYPES` in `models.py` with the real length, wingspan and fuselage radius in metres, and a family pattern in `FAMILIES` if its variants should share it. For a converted model, see [models/README.md](models/README.md), add it to `SOURCES` in `tools/bake_models.py`, and look at the result with `tools/model_sheet.py` before committing: some sources don't survive the reduction.

**Change anything visible.** Look at it with `--screenshot` at 480x320. If a README image would change, run `python tools/media.py` and commit the images.

**Change user-facing text.** Plain words that say what to do next: "NO DATA", "WRONG PASSWORD", "TAP TO CLOSE". No error codes, no stack traces, no em dashes.

## Before you finish

- `python -m tests` passes.
- README, `docs/` and this file updated in the same change when behaviour changed. Search for the old wording.
- Images regenerated if the look changed.
- You said what you couldn't verify, and what it costs in lines or milliseconds if it draws.

## GitHub

- Issues: use `.github/ISSUE_TEMPLATE/`. Blank issues are off.
- Pull requests: fill in every section of `.github/PULL_REQUEST_TEMPLATE.md`, including AI assistance. If a section asks for the person's own words, leave a TODO for them.
- Commits: imperative subject under 72 characters, body says why.
