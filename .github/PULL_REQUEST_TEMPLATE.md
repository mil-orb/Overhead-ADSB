<!-- One change per PR. Coding assistants: fill every section; leave "Why" as a TODO for the person if they haven't given you their reasons. -->

## What changed

## Why

<!-- In your own words: what you ran into, and why this fixes it. -->

Fixes #

## How it was tested

- [ ] `python -m tests` passes
- [ ] Looked at it with `--screenshot` at 480 x 320 (if anything visible changed)
- [ ] Ran on a Pi: <!-- which model and screen, or "no Pi" -->

## What it costs

<!-- For anything that draws: `python tools/bench.py` before and after, and how many screen lines it repaints. "Nothing drawn" is a fine answer. -->

## Screenshots

<!-- For visible changes. Demo traffic only: a picture of live traffic with the time on it gives away roughly where you are. -->

## Checks

- [ ] No new dependency, or the PR says why and the version is at least two weeks old
- [ ] Anything read from the network goes through `parse_aircraft`, `route_entry` or `Weather.refresh` and is checked there
- [ ] No coordinates, flight logs, network names or addresses in code, tests, docs or images
- [ ] README or `docs/` updated if behaviour or a message changed
- [ ] New model files carry their source, license and credit

## AI assistance

- [ ] None
- [ ] Used: <!-- tool, and how much: "wrote the tests", "most of the code" -->
