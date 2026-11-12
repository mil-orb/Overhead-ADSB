"""Turns raw aircraft into an ordered list of sightings for the board, off the UI thread."""
from __future__ import annotations

import math
import os
import threading
import time
from dataclasses import dataclass, field

from .feeds import DEMO_ROUTES, DEMO_SKIES, DEMO_SKY, AdsbFeed, DemoFeed, Routes, Weather
from .sky import Observer, Plane, Sighting, Sky, assess, inbound_eta, locate, route_plausible

VIEW_IN, VIEW_OUT = 0.55, 0.40      # hysteresis so a plane near the threshold doesn't flicker
FAINT_IN, FAINT_OUT = 0.22, 0.14
STICKY = 0.92                       # a plane must be 8% closer to take the row above it

# Which aircraft go on the card. Key, name on the settings screen, one-line description.
MODES = [
    ("view", "IN VIEW", "What you can see now, plus military and planes about to appear"),
    ("overhead", "OVERHEAD", "Nearly straight up, or within 3 miles"),
    ("approaching", "APPROACHING", "Only planes that are getting closer"),
    ("low", "LOW FLYERS", "Below 10,000 ft and in view"),
    ("military", "MILITARY", "Only military aircraft, seen or not"),
    ("all", "EVERYTHING", "Every aircraft in range, seen or not"),
]
DEFAULT_MODE = "view"
OVERHEAD_DEG = 55.0                 # this far above the horizon counts as overhead
OVERHEAD_M = 3 * 1609.344           # so does anything this close
LOW_M = 3048.0                      # 10,000 ft above the ground
CLOSING_MS = 15.0                   # getting closer by at least this many metres a second


@dataclass
class Frame:
    sightings: list = field(default_factory=list)   # ordered for display
    everyone: list = field(default_factory=list)    # every aircraft in the radius, shown or not
    sky: Sky = field(default_factory=Sky)
    in_range: int = 0
    in_view: int = 0
    source: str = ""
    online: bool = True
    stamp: float = 0.0

    def find(self, hexid):
        for s in self.sightings:
            if s.hex == hexid:
                return s
        return None

    def find_any(self, hexid):
        """Like find, but also aircraft that are in range and not in the rotation."""
        for s in self.everyone or self.sightings:
            if s.hex == hexid:
                return s
        return None


def tier(s: Sighting) -> int:
    if s.state == "view":
        return 0
    if s.state == "faint":
        return 1
    if s.plane.military or s.plane.emergency:
        return 2
    return 3 if s.inbound_s is not None else 4


def wanted(mode: str, s: Sighting, obs: Observer) -> bool:
    """Does this sighting belong on the card in the given mode?"""
    seen = s.state in ("view", "faint")
    if mode == "all":
        return True
    if mode == "overhead":
        return s.elev >= OVERHEAD_DEG or s.rng <= OVERHEAD_M
    if mode == "approaching":
        return s.inbound_s is not None or (seen and s.closing >= CLOSING_MS)
    if mode == "low":
        return seen and s.alt_m - obs.elev_m <= LOW_M
    if mode == "military":
        return s.plane.military or s.plane.emergency
    return tier(s) <= 3             # "view": seen, military, or about to appear


class Tracker:
    def __init__(self, obs: Observer, routes: Routes, mode: str = "all"):
        self.obs, self.routes = obs, routes
        self.mode = mode if mode in [m[0] for m in MODES] else DEFAULT_MODE
        self.states: dict = {}
        self.order: list = []

    def compute(self, planes: list[Plane], sky: Sky, now: float, source="", online=True) -> Frame:
        sightings = []
        for p in planes:
            try:
                s = assess(self.obs, sky, p, now)
                prev = self.states.get(p.hex, "hidden")
                if s.score >= VIEW_IN or (prev == "view" and s.score >= VIEW_OUT):
                    s.state = "view"
                elif s.score >= FAINT_IN or (prev in ("view", "faint") and s.score >= FAINT_OUT):
                    s.state = "faint"
                else:
                    s.state = "hidden"
                r = self.routes.get(p.flight)
                if r and route_plausible(r, p.lat, p.lon):
                    s.route = r
                # how fast it is closing, from where it will be in 10 seconds
                later, _, _ = locate(self.obs, *p.at(now + 10.0))
                s.closing = (s.rng - later) / 10.0
                if not all(math.isfinite(v) for v in (s.rng, s.az, s.elev, s.score, s.closing)):
                    continue
            except Exception:
                continue            # one aircraft with numbers the maths can't take: leave it out, keep the rest
            sightings.append(s)
        self.states = {s.hex: s.state for s in sightings}

        # Look ahead only for hidden planes that are coming this way, nearest first.
        hidden = sorted((s for s in sightings if s.state == "hidden" and s.closing > 5.0), key=lambda s: s.rng)[:12]
        for s in hidden:
            s.inbound_s = inbound_eta(self.obs, sky, s.plane, now)

        # The range setting: aircraft beyond it are left out, except ones about to come into view.
        limit = self.obs.range_m * 1.04
        near = [s for s in sightings if s.rng <= limit]
        pool = near + [s for s in sightings if s.rng > limit and s.inbound_s is not None]

        ordered = []
        rank = {h: i for i, h in enumerate(self.order)}
        for t in range(5):
            group = [s for s in pool if tier(s) == t]
            known = sorted((s for s in group if s.hex in rank), key=lambda s: rank[s.hex])
            swapped = True
            while swapped:
                swapped = False
                for i in range(len(known) - 1):
                    if known[i + 1].rng < known[i].rng * STICKY:
                        known[i], known[i + 1] = known[i + 1], known[i]
                        swapped = True
            for s in sorted((s for s in group if s.hex not in rank), key=lambda s: s.rng):
                at = next((i for i, k in enumerate(known) if s.rng < k.rng), len(known))
                known.insert(at, s)
            ordered.extend(known)
        self.order = [s.hex for s in ordered]

        shown = [s for s in ordered if wanted(self.mode, s, self.obs)]
        return Frame(sightings=shown, everyone=ordered, sky=sky, in_range=len(near),
                     in_view=sum(1 for s in near if s.state == "view"),
                     source=source, online=online, stamp=now)


class Engine:
    """Owns the feed, weather and tracker. start() runs them on a background thread."""

    def __init__(self, cfg: dict, demo: bool, data_dir: str):
        self.demo, self.data_dir = demo, data_dir
        self.demo_sky = "day"                  # which of feeds.DEMO_SKIES the demo runs under
        self.failures = 0
        self._last_ok = None                    # when the background loop last finished an update
        self.lock = threading.Lock()
        self.frame = Frame(online=True)
        self.poll = max(2.0, float(cfg.get("poll_s", 5)))
        self._planes: list = []
        self._good = 0.0
        self._wake = threading.Event()
        self._stop = False
        self.routes = Routes(os.path.join(data_dir, "routes.json"), online=not demo)
        if demo:
            self.routes.preload(DEMO_ROUTES)
        self.configure(cfg)

    def configure(self, cfg: dict):
        """Apply settings. Safe to call while running."""
        lat, lon = float(cfg["lat"]), float(cfg["lon"])
        with self.lock:
            miles = max(1.0, float(cfg.get("range_mi", 15)))
            self.obs = Observer(lat, lon, float(cfg.get("elevation_m") or 0.0),
                                float(cfg.get("horizon_deg", 5)), dict(cfg.get("horizon_mask") or {}),
                                range_m=miles * 1609.344)
            self.auto_elev = cfg.get("elevation_m") is None
            # Ask the feed for a wider circle than the range, so aircraft heading in are seen coming.
            query_nm = min(250, int(miles * 0.869 * 1.6 + 6))
            self.feed = DemoFeed(lat, lon) if self.demo else AdsbFeed(lat, lon, query_nm)
            self.weather = Weather(lat, lon, online=not self.demo)
            self.tracker = Tracker(self.obs, self.routes, str(cfg.get("mode") or DEFAULT_MODE))
            self._planes, self._good = [], 0.0
        self._wake.set()

    def step(self, now: float | None = None) -> Frame:
        real = now is None
        now = time.time() if real else now
        with self.lock:
            feed, weather, tracker, obs = self.feed, self.weather, self.tracker, self.obs
        online = True
        try:
            planes = feed.fetch(None if real else now)
            self._planes, self._good = planes, now
        except Exception:
            online = now - self._good < 30      # coast on dead reckoning for half a minute
            planes = self._planes if online else []
        if self.demo:
            sky = Sky(**{**DEMO_SKY, **DEMO_SKIES.get(self.demo_sky, {})})
        else:
            weather.refresh(now)
            if self.auto_elev and weather.elevation is not None:
                obs.elev_m = weather.elevation
            sky = weather.sky(now)
        frame = tracker.compute(planes, sky, now, feed.name, online)
        with self.lock:
            if tracker is self.tracker:         # drop results from before a reconfigure
                self.frame = frame
        return frame

    def latest(self) -> Frame:
        with self.lock:
            return self.frame

    def start(self):
        def loop():
            while not self._stop:
                self._wake.clear()
                try:
                    self.step()
                    self.failures, self._last_ok = 0, time.monotonic()
                except Exception as e:
                    self.failures += 1
                    if self.failures in (1, 10, 100):         # say it in the log, without filling it
                        print("tracker update failed (%d in a row): %s" % (self.failures, type(e).__name__))
                self._wake.wait(self.poll)
        self._last_ok = time.monotonic()
        threading.Thread(target=loop, daemon=True).start()

    def stuck(self) -> bool:
        """True when the background updates have stopped arriving: every one failing, or one hung."""
        return self._last_ok is not None and not self._stop and \
            time.monotonic() - self._last_ok > max(60.0, 6 * self.poll)

    def stop(self):
        self._stop = True
        self._wake.set()
