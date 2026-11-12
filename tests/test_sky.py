"""Run with: python tests/test_sky.py"""
import calendar
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from overhead.feeds import DemoFeed, Routes, parse_aircraft          # noqa: E402
from overhead.sky import (FT, Observer, Plane, Sky, assess, compass, locate, smooth,   # noqa: E402
                 moon_position, sun_position, visibility)
from overhead.tracker import MODES, OVERHEAD_DEG, OVERHEAD_M, Tracker, wanted  # noqa: E402


obs = Observer(40.0, -100.0, 300.0)
clear = Sky(known=True)


def near(a, b, tol):
    assert abs(a - b) <= tol, f"{a} not within {tol} of {b}"


# geometry: 10 km due north, 3 km above the observer
rng, az, elev = locate(obs, 40.0 + 10000 / 111132.0, -100.0, 3300.0)
near(az, 0.0, 0.2)
near(elev, math.degrees(math.atan2(3000, 10000)), 0.3)
near(rng, math.hypot(10000, 3000), 60)
# the quick position math agrees with the exact one out to 150 km
from overhead.sky import enu  # noqa: E402
for dlat, dlon, alt in ((0.3, 0.2, 11000.0), (-1.0, 0.9, 12500.0), (0.02, -0.01, 900.0), (1.2, -0.4, 3000.0)):
    r1, a1, e1 = locate(obs, 40.0 + dlat, -100.0 + dlon, alt)
    e_, n_, u_ = enu(40.0, -100.0, 300.0, 40.0 + dlat, -100.0 + dlon, alt)
    r2 = math.sqrt(e_ * e_ + n_ * n_ + u_ * u_)
    a2, e2 = math.degrees(math.atan2(e_, n_)) % 360, math.degrees(math.asin(u_ / r2))
    assert abs(r1 - r2) < r2 * 0.002 and abs((a1 - a2 + 180) % 360 - 180) < 0.15 and abs(e1 - e2) < 0.05, (r1, r2, a1, a2, e1, e2)
# due east
_, az, _ = locate(obs, 40.0, -100.0 + 0.1, 3300.0)
near(az, 90.0, 0.2)
assert compass(350) == "N" and compass(22.5) == "NNE" and compass(181) == "S"

# sun: 2024-03-20 12:00 UTC at lat 0 lon 0 is close to overhead; midnight UTC it is far below
e, _ = sun_position(0.0, 0.0, 1710936000)
assert e > 85, e
e, _ = sun_position(0.0, 0.0, 1710936000 - 12 * 3600)
assert e < -80, e
# afternoon sun in the northern mid-latitudes is in the south-west
e, a = sun_position(40.0, -100.0, 1718830800)   # 2024-06-19 21:00 UTC, about 3pm local solar
assert 180 < a < 290 and e > 30, (e, a)

# moon: full on 2024-10-17 11:26 UTC, new on 2024-10-02 18:49 UTC
full = calendar.timegm((2024, 10, 17, 11, 26, 0))
new = calendar.timegm((2024, 10, 2, 18, 49, 0))
assert moon_position(40.0, -100.0, full)[2] > 0.99
assert moon_position(40.0, -100.0, new)[2] < 0.01
assert moon_position(40.0, -100.0, new + 5 * 86400)[3] and not moon_position(40.0, -100.0, full + 5 * 86400)[3], "waxing, then waning"
# a full moon is opposite the sun: up when the sun is down, and the other side of the sky
me, ma, _, _ = moon_position(40.0, -100.0, full - 5 * 3600)        # about midnight local time
se, sa = sun_position(40.0, -100.0, full - 5 * 3600)
assert me > 30 and se < -30, (me, se)
assert abs((ma - sa + 180) % 360 - 180) > 150, (ma, sa)
for t in range(0, 30 * 86400, 7919):
    me, ma, lit, _ = moon_position(-33.0, 151.0, full + t)
    assert -90 <= me <= 90 and 0 <= ma < 360 and 0 <= lit <= 1

# visibility ordering
def score(span, ground_km, alt_m, sky=clear, bearing=0.0):
    lat = 40.0 + ground_km * 1000 / 111132.0 * math.cos(math.radians(bearing))
    lon = -100.0 + ground_km * 1000 / (111320.0 * math.cos(math.radians(40))) * math.sin(math.radians(bearing))
    r, az_, el = locate(obs, lat, lon, alt_m)
    return visibility(obs, sky, span, r, az_, el, alt_m)

assert score(34, 2, 10600)[0] > 0.95                      # airliner overhead at cruise
assert score(34, 150, 10600)[1] == "LOW"                  # same airliner 150 km out sits on the horizon
assert score(11, 3, 1000)[0] > 0.9                        # Cessna close by
s, why = score(11, 25, 1200)
assert s < 0.2 and why in ("FAR", "LOW"), (s, why)        # Cessna 25 km away
overcast = Sky(cloud_low=1.0, cloud_total=1.0, known=True)
assert score(34, 2, 10600, overcast) == (0.0, "CLOUD")
assert score(11, 1, 600, overcast)[0] > 0.9               # under the deck is still visible
hazy = Sky(vis_m=5000, known=True)
assert score(34, 30, 3000, hazy)[1] == "HAZE"
assert score(34, 1, 3000, hazy)[0] > 0.9                  # steep sight line clears the haze
night = Sky(sun_elev=-30, known=True)
assert score(11, 12, 2800, night)[0] > score(11, 12, 2800)[0] + 0.2   # lights carry further than a small airframe
sunward = Sky(sun_elev=45, sun_az=0, known=True)
assert score(34, 10, 10300, sunward)[1] == "GLARE"
masked = Observer(40.0, -100.0, 300.0, 5.0, {"N": 40})
r, a_, el = locate(masked, 40.0 + 0.2, -100.0, 6300.0)
assert visibility(masked, clear, 34, r, a_, el, 6300)[1] == "LOW"
assert smooth(1, 0, 0.25) > 0.8 and smooth(0, 1, 0.25) < 0.2

# dead reckoning: 200 m/s north for 10 s is 2 km
p = Plane("abc", lat=40.0, lon=-100.0, alt_m=3000, gs=200, track=0, pos_time=100.0)
lat, lon, _ = p.at(110.0)
near((lat - 40.0) * 111195, 2000, 15)

# feed parsing
ac = {"hex": "ae07d4", "flight": "RCH871  ", "t": "C17", "alt_baro": 24000, "alt_geom": 24500,
      "gs": 420.0, "track": 305.1, "lat": 40.1, "lon": -100.1, "seen_pos": 1.5, "dbFlags": 1,
      "category": "A5", "baro_rate": -640}
p = parse_aircraft(ac, 1000.0)
assert p.military and p.flight == "RCH871" and abs(p.alt_m - 24500 * FT) < 1 and p.pos_time == 998.5
assert parse_aircraft(dict(ac, alt_geom=None, alt_baro="ground"), 0) is None
assert parse_aircraft(dict(ac, seen_pos=120), 0) is None
assert parse_aircraft(dict(ac, dbFlags=0, hex="a00001"), 0).military is False
assert parse_aircraft(dict(ac, squawk="7700"), 0).emergency

# tracker: nearest visible first, military never dropped, order is stable between frames
tr = Tracker(obs, Routes(None, online=False))
feed = DemoFeed(40.0, -100.0)
sky = Sky(cloud_high=0.2, cloud_total=0.2, sun_elev=38, sun_az=120, known=True)
f1 = tr.compute(feed.fetch(1000.0), sky, 1000.0)
states = [s.state for s in f1.sightings]
assert states == sorted(states, key=["view", "faint", "hidden"].index), states
vis = [s for s in f1.sightings if s.state == "view"]
assert len(vis) >= 3 and all(a.rng <= b.rng for a, b in zip(vis, vis[1:]))
assert any(s.plane.military and s.state == "view" for s in f1.sightings)
hidden = [s for s in f1.sightings if s.state == "hidden"]
mil_hidden = [i for i, s in enumerate(hidden) if s.plane.military]
assert mil_hidden == list(range(len(mil_hidden))), "hidden military must lead the hidden group"
f2 = tr.compute(feed.fetch(1002.0), sky, 1002.0)
assert [s.hex for s in f1.sightings][:4] == [s.hex for s in f2.sightings][:4]
# show modes: each one is a filter over the same ordered list
by_mode = {}
for key, name, about in MODES:
    fr = Tracker(obs, Routes(None, online=False), key).compute(feed.fetch(1000.0), sky, 1000.0)
    by_mode[key] = fr.sightings
    assert len(name) <= 12 and len(about) <= 64, (name, about)
everything = by_mode["all"]
assert len(everything) == 11
assert all(s.state != "hidden" or s.plane.military or s.inbound_s is not None for s in by_mode["view"])
assert by_mode["view"] and len(by_mode["view"]) < len(everything)
assert by_mode["overhead"] and all(s.elev >= OVERHEAD_DEG or s.rng <= OVERHEAD_M for s in by_mode["overhead"])
assert {s.plane.flight for s in by_mode["military"]} == {"RCH871", "TOPCT11"}
assert by_mode["low"] and all(s.alt_m - obs.elev_m <= 3048 and s.state != "hidden" for s in by_mode["low"])
assert by_mode["approaching"] and all(s.closing >= 15 or s.inbound_s is not None for s in by_mode["approaching"])
leaving = [s for s in everything if s.closing < 0]
assert leaving and not any(s in by_mode["approaching"] for s in leaving), "planes moving away are not approaching"
for key in by_mode:
    assert [s.hex for s in by_mode[key]] == [s.hex for s in everything if s.hex in {x.hex for x in by_mode[key]}], key
print("modes:", {k: len(v) for k, v in by_mode.items()})

# range setting: nothing beyond it is shown or counted, unless it is about to come into view
near_obs = Observer(40.0, -100.0, 300.0, range_m=15 * 1609.344)
fr = Tracker(near_obs, Routes(None, online=False)).compute(feed.fetch(1000.0), sky, 1000.0)
assert 0 < fr.in_range < f1.in_range, (fr.in_range, f1.in_range)
for s in fr.sightings:
    assert s.rng <= 15 * 1609.344 * 1.04 or s.inbound_s is not None, (s.plane.flight, s.rng / 1609.344)
assert all(s.rng <= 15 * 1609.344 * 1.04 for s in fr.sightings if s.state != "hidden")
far = next(s for s in f1.sightings if s.state == "view" and s.rng > 16 * 1609.344)
r, a_, el = locate(near_obs, *far.plane.at(1000.0))
assert visibility(near_obs, sky, far.plane.span(), r, a_, el, far.alt_m) == (0.0, "FAR")
print("within 15 mi: %d of %d aircraft" % (fr.in_range, f1.in_range))
for s in f1.sightings:
    print(f"{s.plane.flight:8} {s.state:6} {s.reason:5} score={s.score:.2f} rng={s.rng/1609:.1f}mi "
          f"elev={s.elev:.0f} {compass(s.az)} inbound={s.inbound_s}")
print("ok")
