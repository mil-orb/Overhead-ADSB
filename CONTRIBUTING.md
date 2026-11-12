# Contributing

Thanks for looking. Overhead has to stay smooth on a Raspberry Pi Zero, so the bar for changes is "does it still run well on the slow device", then simple, then pretty. Support for more screens and fixes to the visibility estimate are the most useful contributions right now.

## Setup

Python 3.11 or newer and pygame. Nothing else.

```bash
git clone https://github.com/mil-orb/overhead-ADSB
```

```bash
cd overhead-ADSB && python -m venv .venv && .venv/bin/python -m pip install -r requirements.txt
```

```bash
.venv/bin/python -m tests
```

```bash
.venv/bin/python -m overhead --demo
```

On Windows the interpreter is `.venv\Scripts\python`. The demo is simulated traffic: no network, no location, no Pi needed.

Then read [docs/architecture.md](docs/architecture.md) for the file map and [docs/flaps.md](docs/flaps.md) for why the drawing code is shaped the way it is.

## You don't need a Pi

Everything except the last step of display and touch can be worked on in a window. `--screenshot` renders headless to a PNG, `tools/bench.py` times the heavy paths, and the tests drive the whole app (taps, swipes, the settings sheet, joining a stand-in Wi-Fi network) without a screen.

If you do have the hardware, `python tools/pi.py deploy` copies your working tree to it, and `python tools/pi.py screen out.png` shows you what the panel is displaying. [docs/pi-setup.md](docs/pi-setup.md) has the rest.

## Reporting bugs

Use the [bug report template](https://github.com/mil-orb/overhead-ADSB/issues/new/choose). The most useful things are which Pi and screen, the OS version, and the last lines of `journalctl -u overhead`. Leave your coordinates out: nobody needs them to fix a bug.

Security problems go through [SECURITY.md](SECURITY.md), not public issues.

## Suggesting features

Open a [feature request](https://github.com/mil-orb/overhead-ADSB/issues/new?template=feature_request.md) and describe the situation, not only the feature. Some things are out of scope on purpose:

- Anything that needs an account, an API key or a paid data source.
- Dependencies beyond pygame.
- Live video, maps with tiles, or anything else that repaints the whole screen every frame. The SPI link can't carry it.

## Making changes

1. One change per pull request.
2. Write or update a test. The pure parts (`sky.py`, `feeds.py`, `tracker.py`, `models.py`) are easy to test, so they should be. UI changes go in `tests/test_ui.py`, which drives the real app.
3. Run `python -m tests`.
4. If the change draws anything, run `python tools/bench.py` before and after and put the numbers in the PR.
5. If it's visible, look at it with `--screenshot` at 480 x 320, and run `python tools/media.py` if a README image changed.
6. Update README or `docs/` in the same PR when behaviour changes.

### Rules

- **Standard library and pygame only.**
- **Network data is untrusted.** It enters through the parsers in `feeds.py` and nowhere else, and `tests/test_feeds.py` holds the hostile cases.
- **Nothing blocks the main loop**, and animation is driven by elapsed time.
- **Redraw only what changed.** About 2,080 screen lines a second reach the panel.
- **No personal data** in code, tests, docs or images: no real coordinates, flights you logged, network names or addresses.
- **Models keep their licenses** and their credit.

### AI assistance

AI-assisted contributions are welcome. Say so in the PR: which tool, and roughly how much it did. Coding assistants should read [AGENTS.md](AGENTS.md) first. It has the commands that must not be run on a dev machine and the recipes for common changes.

The PR description's "Why" should be yours. An assistant can draft the rest.

## Pull requests

The code is MIT licensed, and anything you contribute to it is under the same license. Models added to `models/` keep their own license, which must allow redistribution.

CI runs the tests on Python 3.11 and 3.13 on Ubuntu, with pygame's dummy video driver.
