"""Network sources: aircraft positions, callsign routes, weather, and the checks on map
imagery. Plus a simulated feed for demos.

Free, keyless APIs only. Coordinates sent out are rounded to 0.01 degree (about 1 km),
which is plenty for a 40 nm query and keeps the exact address off the wire.
"""
from __future__ import annotations

import json
import math
import os
import queue
import re
import struct
import threading
import time
import urllib.error
import urllib.request

from .sky import FT, KT, Plane, Sky, moon_position, sun_position

UA = "flapboard/1.0"
ADSB_SOURCES = [
    ("ADSB.LOL", "https://api.adsb.lol/v2/point/{lat:.2f}/{lon:.2f}/{r:d}"),
    ("AIRPLANES.LIVE", "https://api.airplanes.live/v2/point/{lat:.2f}/{lon:.2f}/{r:d}"),
]
AIRLINE_CALLSIGN = re.compile(r"^[A-Z]{3}\d[A-Z0-9]{0,4}$")


HEX = re.compile(r"^~?[0-9a-f]{6}$")
MAX_BODY = 2_000_000        # bytes; a busy 250 nm query is about 300 kB
MAX_ROUTES = 3000           # callsigns kept in the route cache


def http_json(url: str, timeout: float = 8.0, limit: int = MAX_BODY):
    """GET a URL and parse the JSON. Gives up on a body that is too big or arrives too slowly,
    so a broken or hostile server can't fill the memory or hold the tracker forever."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    deadline = time.monotonic() + 3 * timeout
    parts, size = [], 0
    with urllib.request.urlopen(req, timeout=timeout) as r:
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            size += len(chunk)
            parts.append(chunk)
            if size > limit or time.monotonic() > deadline:
                raise ValueError("response too large or too slow")
    return json.loads(b"".join(parts).decode("utf-8", "replace"))


def http_bytes(url: str, timeout: float = 10.0, limit: int = 300_000) -> bytes:
    """GET a URL and return the body, with the same size and time limits as http_json.
    Only for images, which are checked with image_header before anything decodes them."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "image/png,image/jpeg"})
    deadline = time.monotonic() + 3 * timeout
    parts, size = [], 0
    with urllib.request.urlopen(req, timeout=timeout) as r:
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            size += len(chunk)
            parts.append(chunk)
            if size > limit or time.monotonic() > deadline:
                raise ValueError("response too large or too slow")
    return b"".join(parts)


PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"
_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def image_header(data) -> tuple[str, int, int] | None:
    """("png" or "jpeg", width, height) read from an image file's header, or None if it is
    neither. This runs before any decoder sees the bytes: a file of a few kB can claim to be
    a picture of 60000 x 60000, and the decoder would try to allocate it."""
    if not isinstance(data, (bytes, bytearray)) or len(data) < 24:
        return None
    if data[:8] == PNG_MAGIC:
        if data[12:16] != b"IHDR":
            return None
        w, h = struct.unpack(">II", data[16:24])
        return "png", w, h
    if data[:3] != JPEG_MAGIC:
        return None
    i, n = 2, len(data)
    for _ in range(200):                       # markers before the frame header; real files have a handful
        while i < n and data[i] == 0xFF and i + 1 < n and data[i + 1] == 0xFF:
            i += 1                             # fill bytes
        if i + 4 > n or data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xD8 or 0xD0 <= marker <= 0xD7 or marker == 0x01:
            i += 2
            continue
        if marker == 0xD9 or marker == 0xDA:   # end of image or start of scan before any frame header
            return None
        length = struct.unpack(">H", data[i + 2:i + 4])[0]
        if length < 2:
            return None
        if marker in _SOF:
            if i + 9 > n:
                return None
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return "jpeg", w, h
        i += 2 + length
    return None


def _num(v, lo=-math.inf, hi=math.inf):
    """A finite number within [lo, hi], or None. JSON allows NaN, Infinity and 1e999, and
    booleans are numbers in Python: none of those get through."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    try:
        v = float(v)
    except OverflowError:
        return None
    return v if math.isfinite(v) and lo <= v <= hi else None


def _text(v, n):
    """Printable text of at most n characters, upper case. Anything that isn't a string is ''.
    Control characters are dropped: the display raises on a NUL."""
    if not isinstance(v, str):
        return ""
    return "".join(c for c in v[:4 * n] if c.isprintable()).strip().upper()[:n]


def parse_aircraft(ac, fetched_at: float) -> Plane | None:
    """readsb-style aircraft JSON -> Plane. Returns None for anything not airborne with a fresh fix,
    and for any record with a field that is the wrong type, not finite or out of range.

    This is the only way feed data gets into the program. Everything past here can rely on
    finite numbers in sane ranges and short printable strings.
    """
    if not isinstance(ac, dict):
        return None
    lat, lon = _num(ac.get("lat"), -90, 90), _num(ac.get("lon"), -180, 180)
    if lat is None or lon is None:
        return None
    alt = _num(ac.get("alt_geom"), -2000, 150000)
    if alt is None:
        alt = _num(ac.get("alt_baro"), -2000, 150000)      # the string "ground" falls out here
    if alt is None:
        return None
    seen = _num(ac.get("seen_pos"), 0, 86400)
    if seen is None:
        seen = _num(ac.get("seen"), 0, 86400) or 0.0
    if seen > 45:
        return None
    hexid = ac.get("hex")
    hexid = hexid.lower().strip() if isinstance(hexid, str) else ""
    if not HEX.match(hexid):
        return None
    mil = bool(int(_num(ac.get("dbFlags"), 0, 2 ** 31) or 0) & 1)
    if not mil and not hexid.startswith("~"):
        mil = 0xAE0000 <= int(hexid, 16) <= 0xAFFFFF     # US military address block
    rate = _num(ac.get("geom_rate"), -30000, 30000)
    if rate is None:
        rate = _num(ac.get("baro_rate"), -30000, 30000) or 0.0
    squawk, emergency = ac.get("squawk"), ac.get("emergency")
    emerg = squawk in ("7500", "7600", "7700") or \
        (isinstance(emergency, str) and emergency.lower() not in ("none", "", "null"))
    return Plane(
        hex=hexid,
        flight=_text(ac.get("flight"), 8),
        reg=_text(ac.get("r"), 12),
        type=_text(ac.get("t"), 4),
        desc=_text(ac.get("desc"), 40),
        owner=_text(ac.get("ownOp"), 40),
        category=_text(ac.get("category"), 2),
        military=mil, emergency=emerg,
        lat=lat, lon=lon, alt_m=alt * FT,
        gs=(_num(ac.get("gs"), 0, 2000) or 0.0) * KT,
        track=(_num(ac.get("track"), -360, 720) or 0.0) % 360.0,
        vrate=rate * FT / 60.0,
        roll=max(-75.0, min(75.0, _num(ac.get("roll"), -180, 180) or 0.0)),
        pos_time=fetched_at - seen,
    )


def parse_feed(data, fetched_at: float) -> list[Plane]:
    """Every usable aircraft in a response. One bad record never costs the rest."""
    ac = data.get("ac") if isinstance(data, dict) else None
    if not isinstance(ac, list):
        raise ValueError("no aircraft list in the response")
    planes = []
    for a in ac[:4000]:
        try:
            p = parse_aircraft(a, fetched_at)
        except Exception:
            p = None
        if p:
            planes.append(p)
    return planes


class AdsbFeed:
    """Polls a point query. Falls over to the next source when one fails."""

    def __init__(self, lat, lon, radius_nm):
        self.lat, self.lon, self.radius = lat, lon, int(max(5, min(250, radius_nm)))
        self.i = 0
        self.name = ADSB_SOURCES[0][0]

    def fetch(self, now: float | None = None) -> list[Plane]:
        err = None
        for _ in range(len(ADSB_SOURCES)):
            name, url = ADSB_SOURCES[self.i]
            try:
                data = http_json(url.format(lat=self.lat, lon=self.lon, r=self.radius))
                planes = parse_feed(data, time.time())
                self.name = name
                return planes
            except Exception as e:      # network, HTTP, JSON: all mean "try the other one"
                err = e
                self.i = (self.i + 1) % len(ADSB_SOURCES)
        raise err or RuntimeError("no source")


def clean_route(e) -> dict | None:
    """A route cache entry with every field checked, or None. Used for answers from the
    network and for the file on disk, which is input too."""
    if not isinstance(e, dict):
        return None
    ts = _num(e.get("ts"), 0, 1e11)
    if ts is None:
        return None
    out = {"ts": ts}
    for key, n in (("from", 3), ("to", 3), ("from_city", 40), ("to_city", 40), ("airline", 40)):
        v = _text(e.get(key), n)
        if v:
            out[key] = v
    for key in ("o", "d"):
        v = e.get(key)
        if isinstance(v, (list, tuple)) and len(v) == 2:
            la, lo = _num(v[0], -90, 90), _num(v[1], -180, 180)
            if la is not None and lo is not None:
                out[key] = [la, lo]
    if ("o" in out) != ("d" in out):
        out.pop("o", None)
        out.pop("d", None)
    return out


def route_entry(answer, now: float) -> dict:
    """adsbdb's answer for a callsign -> cache entry. An answer with no route is a remembered miss."""
    r = answer.get("response") if isinstance(answer, dict) else None
    fr = r.get("flightroute") if isinstance(r, dict) else None
    if not isinstance(fr, dict):
        return {"ts": now}
    o, d, line = (fr.get(k) if isinstance(fr.get(k), dict) else {} for k in ("origin", "destination", "airline"))
    return clean_route({
        "ts": now,
        "from": o.get("iata_code") or o.get("icao_code"), "to": d.get("iata_code") or d.get("icao_code"),
        "from_city": o.get("municipality"), "to_city": d.get("municipality"), "airline": line.get("name"),
        "o": [o.get("latitude"), o.get("longitude")], "d": [d.get("latitude"), d.get("longitude")],
    }) or {"ts": now}


class Routes:
    """Callsign -> origin/destination/airline, from adsbdb.com, cached on disk.

    get() never blocks. Unknown callsigns are queued and looked up at 1 per second.
    """
    TTL_HIT, TTL_MISS = 14 * 86400, 2 * 86400

    def __init__(self, path: str | None, online: bool = True):
        self.path, self.online = path, online
        self.cache: dict = {}
        self.q: queue.Queue = queue.Queue()
        self.pending: set = set()
        self.lock = threading.Lock()
        if path and os.path.exists(path):
            try:
                if os.path.getsize(path) <= MAX_BODY:
                    with open(path, "r", encoding="utf-8") as f:
                        raw = json.load(f)
                    for cs, e in (raw.items() if isinstance(raw, dict) else ()):
                        e = clean_route(e)
                        if e and isinstance(cs, str) and AIRLINE_CALLSIGN.match(cs):
                            self.cache[cs] = e
                    self._trim()
            except Exception:
                self.cache = {}
        if online:
            threading.Thread(target=self._work, daemon=True).start()

    def _trim(self):
        """Forget expired entries, then the oldest, so the cache and its file stay small."""
        now = time.time()
        for cs in [c for c, e in self.cache.items() if now - e["ts"] > self.TTL_HIT]:
            del self.cache[cs]
        if len(self.cache) > MAX_ROUTES:
            for cs in sorted(self.cache, key=lambda c: self.cache[c]["ts"])[:len(self.cache) - MAX_ROUTES]:
                del self.cache[cs]

    def preload(self, table: dict):
        for k, v in table.items():
            self.cache[k] = dict(v, ts=time.time())

    def get(self, callsign: str) -> dict | None:
        if not callsign:
            return None
        with self.lock:
            hit = self.cache.get(callsign)
            fresh = hit and time.time() - hit.get("ts", 0) < (self.TTL_HIT if hit.get("from") else self.TTL_MISS)
            if not fresh and self.online and callsign not in self.pending and len(self.pending) < 60 \
                    and AIRLINE_CALLSIGN.match(callsign):
                self.pending.add(callsign)
                self.q.put(callsign)
        return hit if hit and hit.get("from") else None

    def _work(self):
        dirty = 0
        while True:
            cs = self.q.get()
            try:
                entry = route_entry(http_json("https://api.adsbdb.com/v0/callsign/" + cs, limit=100_000), time.time())
            except urllib.error.HTTPError as e:
                entry = {"ts": time.time()} if e.code == 404 else None      # 404: no such callsign, remember that
                if e.code == 429:
                    time.sleep(20)
            except Exception:
                entry = None                  # offline: forget it and retry later
            with self.lock:
                self.pending.discard(cs)
                if entry:
                    self.cache[cs] = entry
                    dirty += 1
            if dirty >= 5 and self.q.empty():
                self.save()
                dirty = 0
            time.sleep(1.1)

    def save(self):
        if not self.path:
            return
        try:
            with self.lock:
                self._trim()
                blob = json.dumps(self.cache)
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(blob)
            os.replace(tmp, self.path)
        except Exception:
            pass


class Weather:
    """Cloud layers and visibility from Open-Meteo, refreshed every 10 minutes."""

    def __init__(self, lat, lon, online: bool = True):
        self.lat, self.lon, self.online = lat, lon, online
        self.data: dict | None = None
        self.elevation: float | None = None
        self.next = 0.0

    def refresh(self, now: float):
        if not self.online or now < self.next:
            return
        self.next = now + 600
        try:
            j = http_json(
                "https://api.open-meteo.com/v1/forecast?latitude={:.2f}&longitude={:.2f}"
                "&current=cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high,visibility"
                .format(self.lat, self.lon), limit=100_000)
            cur = j.get("current") if isinstance(j, dict) else None
            if not isinstance(cur, dict):
                raise ValueError("no current weather in the response")
            data = {k: _num(cur.get(k), 0, 100) for k in ("cloud_cover", "cloud_cover_low", "cloud_cover_mid", "cloud_cover_high")}
            data["visibility"] = _num(cur.get("visibility"), 0, 1e6)
            self.data = data if data["cloud_cover"] is not None else None
            elevation = _num(j.get("elevation"), -500, 9000)
            if elevation is not None:
                self.elevation = elevation
        except Exception:
            self.next = now + 90

    def sky(self, now: float) -> Sky:
        se, sa = sun_position(self.lat, self.lon, now)
        me, ma, lit, waxing = moon_position(self.lat, self.lon, now)
        month = time.gmtime(now).tm_mon
        extra = dict(moon_elev=me, moon_az=ma, moon_lit=lit, moon_waxing=waxing,
                     growing=(4 <= month <= 10) == (self.lat >= 0))
        d = self.data
        if not d:
            return Sky(sun_elev=se, sun_az=sa, known=False, **extra)

        def pct(k):
            return (d.get(k) or 0.0) / 100.0

        vis = d.get("visibility")
        return Sky(cloud_low=pct("cloud_cover_low"), cloud_mid=pct("cloud_cover_mid"),
                   cloud_high=pct("cloud_cover_high"), cloud_total=pct("cloud_cover"),
                   vis_m=vis if vis is not None else 40000.0,
                   sun_elev=se, sun_az=sa, known=True, **extra)


# ---------------------------------------------------------------- demo ------

# flight, reg, type, desc, owner, cat, mil, alt_ft, gs_kt, track, offset_km (right of track),
# seconds before closest approach at t=0, loop period in seconds
_DEMO = [
    ("UAL328", "N37408", "B739", "BOEING 737-900", "", "A3", False, 34000, 462, 78, 3.5, 25, 900),
    ("N172SP", "N172SP", "C172", "CESSNA 172 SKYHAWK", "PRIVATE OWNER", "A1", False, 2600, 104, 200, 1.6, 40, 700),
    ("RCH871", "07-7178", "C17", "BOEING C-17A GLOBEMASTER III", "UNITED STATES AIR FORCE", "A5", True, 24000, 418, 305, 9.0, 55, 1000),
    ("DAL1482", "N301DV", "A321", "AIRBUS A321", "", "A3", False, 31000, 448, 12, 22.0, 80, 820),
    ("N908PD", "N908PD", "EC35", "AIRBUS H135", "COUNTY SHERIFF", "A7", False, 1400, 92, 140, 4.2, -30, 640),
    ("AAL95", "N781AN", "B772", "BOEING 777-200ER", "", "A5", False, 38000, 488, 250, 44.0, 20, 950),
    ("FDX1203", "N120FE", "B763", "BOEING 767-300F", "", "A5", False, 11000, 310, 165, 2.0, 380, 900),
    ("SWA2210", "N8704Q", "B38M", "BOEING 737 MAX 8", "", "A3", False, 5200, 248, 330, 38.0, 60, 860),
    ("TOPCT11", "59-1482", "K35R", "BOEING KC-135R STRATOTANKER", "UNITED STATES AIR FORCE", "A5", True, 27000, 440, 95, 62.0, 100, 1100),
    ("JBU615", "N653JB", "A320", "AIRBUS A320", "", "A3", False, 36000, 455, 215, 58.0, -140, 990),
    ("ASA417", "N284AK", "B739", "BOEING 737-900", "", "A3", False, 29000, 440, 290, 30.0, 150, 870),
]
_DEMO_HEX = ["a4b1c2", "a1e5f0", "ae07d4", "a33d91", "ac9f20", "aa71be", "a05c44", "ab6e12",
             "ae01c7", "a8a3d5", "a2f0b8"]
DEMO_ROUTES = {
    "UAL328": {"from": "SFO", "to": "ORD", "airline": "UNITED AIRLINES"},
    "DAL1482": {"from": "ATL", "to": "MSP", "airline": "DELTA AIR LINES"},
    "AAL95": {"from": "JFK", "to": "LAX", "airline": "AMERICAN AIRLINES"},
    "FDX1203": {"from": "MEM", "to": "ORD", "airline": "FEDEX EXPRESS"},
    "SWA2210": {"from": "MDW", "to": "DEN", "airline": "SOUTHWEST AIRLINES"},
    "JBU615": {"from": "BOS", "to": "SFO", "airline": "JETBLUE AIRWAYS"},
    "ASA417": {"from": "SEA", "to": "ORD", "airline": "ALASKA AIRLINES"},
}
DEMO_SKY = dict(cloud_low=0.08, cloud_mid=0.0, cloud_high=0.25, cloud_total=0.3,
                vis_m=32000.0, sun_elev=38.0, sun_az=120.0, known=True)
# Other skies for looking at the plane view in the demo: python -m overhead --demo --sky dusk
DEMO_SKIES = {
    "day": {},
    "dusk": dict(sun_elev=-1.5, sun_az=262.0, cloud_low=0.15, cloud_high=0.3, cloud_total=0.4,
                 moon_elev=24.0, moon_az=130.0, moon_lit=0.8),
    "night": dict(sun_elev=-30.0, sun_az=330.0, cloud_low=0.05, cloud_high=0.0, cloud_total=0.05,
                  moon_elev=41.0, moon_az=200.0, moon_lit=0.62),
    "overcast": dict(sun_elev=30.0, cloud_low=0.9, cloud_mid=0.8, cloud_high=0.5, cloud_total=1.0, vis_m=9000.0),
}


class DemoFeed:
    """Simulated traffic on straight tracks past the observer. No network needed."""
    name = "DEMO"

    def __init__(self, lat, lon):
        self.lat, self.lon = lat, lon
        self.t0 = None

    def fetch(self, now: float | None = None) -> list[Plane]:
        now = time.time() if now is None else now
        if self.t0 is None:
            self.t0 = now
        t = now - self.t0
        out = []
        for hx, (fl, reg, typ, desc, own, cat, mil, alt, gs, trk, off, lead, period) in zip(_DEMO_HEX, _DEMO):
            te = ((t - lead + period / 2) % period) - period / 2     # seconds from closest approach
            s, tr = gs * KT * te, math.radians(trk)
            e = s * math.sin(tr) + off * 1000 * math.cos(tr)
            n = s * math.cos(tr) - off * 1000 * math.sin(tr)
            climb = -6.0 if fl == "FDX1203" else 0.0
            out.append(Plane(
                hex=hx, flight=fl, reg=reg, type=typ, desc=desc, owner=own, category=cat, military=mil,
                lat=self.lat + n / 111320.0,
                lon=self.lon + e / (111320.0 * math.cos(math.radians(self.lat))),
                alt_m=max(600.0, alt * FT + climb * te), gs=gs * KT, track=trk, vrate=climb, pos_time=now))
        return out
