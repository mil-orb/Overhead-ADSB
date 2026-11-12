"""Everything from the network is untrusted: python tests/test_feeds.py

Each case here is a response a broken or hostile server could send. None of them may
crash the display, freeze the card, or get odd values past the parser.
"""
import json
import math
import os
import sys
import tempfile
import time

os.environ["SDL_VIDEODRIVER"] = "dummy"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame  # noqa: E402

from overhead import feeds, models  # noqa: E402
from overhead.board import Fonts, text_line  # noqa: E402
from overhead.feeds import Routes, Weather, clean_route, parse_aircraft, parse_feed, route_entry  # noqa: E402
from overhead.sky import Observer, Sky, route_plausible  # noqa: E402
from overhead.tracker import Tracker  # noqa: E402
from overhead.wifi import NmWifi, parse_scan  # noqa: E402

good = {"hex": "a1b2c3", "flight": "UAL328  ", "r": "N37408", "t": "B739", "desc": "BOEING 737-900", "ownOp": "UNITED AIRLINES",
        "alt_baro": 34000, "alt_geom": 34500, "gs": 450.0, "track": 78.0, "lat": 40.1, "lon": -100.1, "seen_pos": 1.0,
        "category": "A3", "baro_rate": 0, "roll": 1.5}
assert parse_aircraft(good, 1000.0).flight == "UAL328"

# json.loads accepts these, so the parser has to refuse them
weird = json.loads('{"nan": NaN, "inf": Infinity, "ninf": -Infinity, "big": 1e999, "huge": 1' + "0" * 400 + "}")
assert math.isnan(weird["nan"]) and math.isinf(weird["big"])
for key in ("lat", "lon"):
    for bad in list(weird.values()) + [True, "40.1", None, [40.1], {"v": 1}, 91.0 if key == "lat" else 181.0, -200.0]:
        assert parse_aircraft(dict(good, **{key: bad}), 0) is None, (key, bad)
for bad in list(weird.values()) + [True, "ground", 400000, -9000]:
    assert parse_aircraft(dict(good, alt_geom=bad, alt_baro=bad), 0) is None, bad
# a bad optional field is dropped and the aircraft kept, with every number finite
for key in ("gs", "track", "geom_rate", "baro_rate", "roll", "dbFlags", "seen", "squawk", "emergency"):
    for bad in list(weird.values()) + [True, "x", None, [1], {"a": 1}, -1e12, 1e12]:
        p = parse_aircraft(dict(good, **{key: bad}), 1000.0)
        assert p is not None, (key, bad)
        nums = (p.lat, p.lon, p.alt_m, p.gs, p.track, p.vrate, p.roll, p.pos_time)
        assert all(isinstance(v, float) and math.isfinite(v) for v in nums), (key, bad, nums)
        assert 0 <= p.gs < 1100 and 0 <= p.track < 360 and abs(p.vrate) < 160 and abs(p.roll) <= 75, (key, bad)
        assert p.military is False and p.emergency in (False, True)
assert parse_aircraft(dict(good, seen_pos=weird["nan"], seen=weird["inf"]), 1000.0).pos_time == 1000.0

# the address is six hex digits, nothing else
for bad in ("", "zzzzzz", "a1b2c3d4", "a1b2c", "../../x", "a1b2c3\x00", 123456, None, ["a1b2c3"], "~", "~~12345"):
    assert parse_aircraft(dict(good, hex=bad), 0) is None, bad
assert parse_aircraft(dict(good, hex=" A1B2C3 "), 0).hex == "a1b2c3"
assert parse_aircraft(dict(good, hex="~abc123"), 0).hex == "~abc123"

# text: printable, short, upper case, whatever arrives
nasty = ["BOEING\x00737", "A" * 200000, "\x1b[2J\x07", "line\nbreak\ttab", "‮RTL", 12345, None, ["x"], {"a": 1}, True, "  ok  "]
for key, limit in (("flight", 8), ("r", 12), ("t", 4), ("desc", 40), ("ownOp", 40), ("category", 2)):
    for bad in nasty:
        p = parse_aircraft(dict(good, **{key: bad}), 0)
        v = getattr(p, {"r": "reg", "t": "type", "ownOp": "owner"}.get(key, key))
        assert isinstance(v, str) and len(v) <= limit and v.isprintable() and v == v.upper() and "\x00" not in v, (key, bad, v)
assert parse_aircraft(dict(good, desc="BOEING\x00737"), 0).desc == "BOEING737"
for bad in (None, 5, "x", [], [good], True):
    assert parse_aircraft(bad, 0) is None

# one bad record never costs the others; a response with no list is an error, so the next source is tried
mixed = {"ac": [good, None, 5, "x", [], {"hex": "a00002"}, dict(good, hex="a00003", lat=weird["big"]),
                dict(good, hex="a00004", dbFlags=weird["big"]), dict(good, hex="a00005", flight="X\x00Y")]}
got = parse_feed(mixed, 1000.0)
assert [p.hex for p in got] == ["a1b2c3", "a00004", "a00005"], [p.hex for p in got]
for bad in (None, [], "x", {}, {"ac": None}, {"ac": "x"}, {"ac": {"a": 1}}):
    try:
        parse_feed(bad, 0)
        raise AssertionError("accepted %r" % (bad,))
    except ValueError:
        pass
assert len(parse_feed({"ac": [dict(good, hex="%06x" % i) for i in range(6000)]}, 0)) == 4000, "a flood is cut off"

# the tracker survives whatever does get through, and leaves out only the aircraft it can't place
obs = Observer(40.0, -100.0, 300.0)
tr = Tracker(obs, Routes(None, online=False), "all")
planes = parse_feed({"ac": [good, dict(good, hex="a00006")]}, 1000.0)
planes[1].gs = float("inf")                       # as if a future field slipped past the parser
frame = tr.compute(planes, Sky(known=True), 1000.0)
assert [s.hex for s in frame.everyone or frame.sightings] == ["a1b2c3"]

# drawing text never raises, whatever is in it
pygame.display.init()
pygame.font.init()
pygame.display.set_mode((1, 1))
fonts = Fonts(None)
for bad in nasty[:5] + ["", " ", "\x00", "\ud800"]:
    out = text_line(fonts, bad, 10, (255, 255, 255))
    assert out is None or out[0].get_width() < 4000, bad

# type codes: only real ones pick a model file or are remembered
before = len(models._built)
for bad in ("../../etc/passwd", "A" * 5000, "B7\x0038", "b738; rm", ""):
    m, exact = models.model_for(bad, "A3")
    assert m.faces and not exact, bad
assert len(models._built) <= before + 1, "odd type strings must not pile up in the cache"

# routes: answers from adsbdb, and the cache file, are checked field by field
now = 1_700_000_000.0
ok = {"response": {"flightroute": {
    "airline": {"name": "United Airlines"},
    "origin": {"iata_code": "ord", "municipality": "Chicago", "latitude": 41.97, "longitude": -87.9},
    "destination": {"iata_code": "SFO", "municipality": "San Francisco", "latitude": 37.62, "longitude": -122.37}}}}
e = route_entry(ok, now)
assert e["from"] == "ORD" and e["to"] == "SFO" and e["airline"] == "UNITED AIRLINES" and e["o"] == [41.97, -87.9]
for bad in (None, 5, [], "x", {}, {"response": "unknown callsign"}, {"response": {"flightroute": None}},
            {"response": {"flightroute": "x"}}, {"response": {"flightroute": {"origin": 5, "destination": [], "airline": "x"}}}):
    assert route_entry(bad, now) == {"ts": now}, bad
bent = json.loads(json.dumps(ok))
bent["response"]["flightroute"]["origin"].update(latitude="41.97", municipality="CHI\x00CAGO" + "X" * 500, iata_code=["ORD"])
bent["response"]["flightroute"]["destination"]["longitude"] = weird["big"]
bent["response"]["flightroute"]["airline"]["name"] = "A\x00B"
e = route_entry(bent, now)
assert "o" not in e and "d" not in e and "from" not in e and e["airline"] == "AB" and len(e["from_city"]) == 40, e
assert route_plausible(e, 40.0, -100.0)
assert route_plausible({"o": ["x", 1], "d": [1, 2]}, 40.0, -100.0) is False, "a damaged entry is refused, not raised"
for bad in (None, 5, "x", [], {"from": "ORD"}, {"ts": "x"}, {"ts": weird["nan"]}):
    assert clean_route(bad) is None, bad
assert clean_route({"ts": now, "from": "ORD", "o": [1e999, 2], "d": [3, 4]}) == {"ts": now, "from": "ORD"}

# the cache file is input too: bad entries are dropped on load, old ones expire, and it can't grow without limit
tmp = tempfile.mkdtemp()
path = os.path.join(tmp, "sub", "routes.json")              # a folder that doesn't exist yet
os.makedirs(os.path.dirname(path))
fresh = time.time()
with open(path, "w", encoding="utf-8") as f:
    json.dump({"UAL328": {"ts": fresh, "from": "ORD", "to": "SFO", "o": ["41.9", 1], "d": [2, 3]},
               "DAL12": {"ts": fresh - 30 * 86400, "from": "ATL", "to": "LAX"},
               "../x": {"ts": fresh, "from": "AAA", "to": "BBB"}, "AAL1": "nope", "SWA5": {"ts": fresh, "airline": "S\x00W"}}, f)
r = Routes(path, online=False)
assert set(r.cache) == {"UAL328", "SWA5"}, set(r.cache)
assert "o" not in r.cache["UAL328"] and r.cache["SWA5"]["airline"] == "SW"
for i in range(feeds.MAX_ROUTES + 500):
    r.cache["TST%d" % i] = {"ts": fresh - i}
os.rmdir(os.path.dirname(path)) if not os.listdir(os.path.dirname(path)) else None
r.save()
assert os.path.exists(path), "saving creates the folder if it has to"
assert len(json.load(open(path))) == feeds.MAX_ROUTES
with open(path, "w") as f:
    f.write("[" * 100000)
assert Routes(path, online=False).cache == {}, "an unreadable cache file is ignored"
os.remove(path)

# weather: odd answers are refused and the last good one, or a clear sky, is used
w = Weather(40.0, -100.0, online=True)
answers = []
feeds_http, feeds.http_json = feeds.http_json, lambda url, **kw: answers.pop(0)
try:
    for bad in ({"current": [1]}, {"current": "x"}, [], None, {"current": {"cloud_cover": "50"}}, {"current": {"cloud_cover": weird["nan"]}}):
        answers.append(bad)
        w.next = 0
        w.refresh(1000.0)
        sky = w.sky(1000.0)
        assert sky.known is False and math.isfinite(sky.sun_elev), bad
    answers.append({"elevation": weird["big"], "current": {"cloud_cover": 250, "cloud_cover_low": 40, "visibility": weird["inf"]}})
    w.next = 0
    w.refresh(1000.0)
    assert w.elevation is None and w.sky(1000.0).known is False
    answers.append({"elevation": 187.0, "current": {"cloud_cover": 60, "cloud_cover_low": 40, "cloud_cover_mid": True,
                                                    "cloud_cover_high": "x", "visibility": 24000}})
    w.next = 0
    w.refresh(1000.0)
    sky = w.sky(1000.0)
    assert w.elevation == 187.0 and sky.known and sky.cloud_total == 0.6 and sky.cloud_low == 0.4 and sky.cloud_mid == 0.0 \
        and sky.cloud_high == 0.0 and sky.vis_m == 24000.0
finally:
    feeds.http_json = feeds_http


# http: a body that is too big, or drips in too slowly, is abandoned
class Body:
    def __init__(self, chunks, delay=0.0):
        self.chunks, self.delay = list(chunks), delay

    def read(self, n):
        time.sleep(self.delay)
        return self.chunks.pop(0) if self.chunks else b""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


real_open = feeds.urllib.request.urlopen
try:
    feeds.urllib.request.urlopen = lambda req, timeout: Body([b'{"ac": [', b"]}"])
    assert feeds.http_json("https://example.invalid/") == {"ac": []}
    feeds.urllib.request.urlopen = lambda req, timeout: Body([b" " * 65536] * 100)
    for kw in ({}, {"limit": 1000}):
        try:
            feeds.http_json("https://example.invalid/", **kw)
            raise AssertionError("accepted a %d byte body" % (65536 * 100))
        except ValueError:
            pass
    feeds.urllib.request.urlopen = lambda req, timeout: Body([b" "] * 1000, delay=0.02)
    t0 = time.time()
    try:
        feeds.http_json("https://example.invalid/", timeout=0.05)
        raise AssertionError("waited for a body that drips in")
    except ValueError:
        pass
    assert time.time() - t0 < 2.0
finally:
    feeds.urllib.request.urlopen = real_open

# Wi-Fi names from a scan: odd ones are not offered, and none can be read as an option
scan = "\n".join(["*:Home:80:WPA2", " :-o evil:70:WPA2", " ::60:WPA2", " :" + "x" * 80 + ":50:WPA2", " :Caf\\: 2.4:40:--",
                  " :tab\there:30:WPA2", " :Home:20:WPA2"])
nets = parse_scan(scan, {"Home"})
assert [n.ssid for n in nets] == ["Home", "Caf: 2.4"], [n.ssid for n in nets]
assert nets[0].in_use and nets[0].saved and not nets[1].secure
calls = []
nm = NmWifi()
nm._run = lambda args, timeout=25: calls.append(args) or type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
assert nm.connect("-evil", "password1")[0] is False and nm.connect("", None)[0] is False
assert nm.connect("Home", "short")[0] is False and nm.connect("Home", "x" * 64)[0] is False
assert calls == [], "nothing is run for a name or password that can't be right"
assert nm.connect("Home", "password1") == (True, "CONNECTED")
assert calls[0] == ["device", "wifi", "connect", "Home", "password", "password1"]
assert calls[1][:4] == ["connection", "modify", "id", "Home"]
print("ok")
