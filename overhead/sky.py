"""Where an aircraft sits in the observer's sky, and whether a person could actually see it.

Everything here is plain math with no I/O, so it can be tested on its own.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

FT = 0.3048
KT = 0.514444
NM = 1852.0
_A = 6378137.0            # WGS84 semi-major axis
_E2 = 6.69437999014e-3    # WGS84 eccentricity squared

COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
           "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]

# Wingspan in metres. ADS-B emitter category first, a few specific types override it.
SPAN_BY_CAT = {"A1": 11, "A2": 20, "A3": 34, "A4": 38, "A5": 60, "A6": 12, "A7": 13,
               "B1": 17, "B2": 20, "B4": 9, "B6": 3}
SPAN_BY_TYPE = {"A388": 80, "B748": 68, "B744": 64, "B77W": 65, "B772": 61, "B789": 60,
                "B788": 60, "A359": 65, "A333": 60, "A332": 60, "C17": 52, "C5M": 68,
                "K35R": 40, "C130": 40, "C30J": 40, "B52": 56, "H60": 16}


def compass(az: float) -> str:
    return COMPASS[int((az % 360) / 22.5 + 0.5) % 16]


def smooth(a: float, b: float, x: float) -> float:
    """Smoothstep from a to b. Works with a > b for a falling edge."""
    if a == b:
        return 1.0 if x >= b else 0.0
    t = (x - a) / (b - a)
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    return t * t * (3 - 2 * t)


def _ecef(lat, lon, h):
    la, lo = math.radians(lat), math.radians(lon)
    n = _A / math.sqrt(1 - _E2 * math.sin(la) ** 2)
    return ((n + h) * math.cos(la) * math.cos(lo),
            (n + h) * math.cos(la) * math.sin(lo),
            (n * (1 - _E2) + h) * math.sin(la))


def enu(obs_lat, obs_lon, obs_h, lat, lon, h):
    """East, north, up in metres from the observer to a point. Earth curvature included."""
    ox, oy, oz = _ecef(obs_lat, obs_lon, obs_h)
    tx, ty, tz = _ecef(lat, lon, h)
    dx, dy, dz = tx - ox, ty - oy, tz - oz
    la, lo = math.radians(obs_lat), math.radians(obs_lon)
    e = -math.sin(lo) * dx + math.cos(lo) * dy
    n = -math.sin(la) * math.cos(lo) * dx - math.sin(la) * math.sin(lo) * dy + math.cos(la) * dz
    u = math.cos(la) * math.cos(lo) * dx + math.cos(la) * math.sin(lo) * dy + math.sin(la) * dz
    return e, n, u


def haversine(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * 6371000.0 * math.asin(min(1.0, math.sqrt(a)))


def sun_position(lat: float, lon: float, t: float | None = None):
    """Sun elevation and azimuth in degrees (azimuth clockwise from north). Good to ~0.1 degree."""
    t = time.time() if t is None else t
    n = t / 86400.0 + 2440587.5 - 2451545.0
    L = (280.460 + 0.9856474 * n) % 360
    g = math.radians((357.528 + 0.9856003 * n) % 360)
    lam = math.radians(L + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g))
    eps = math.radians(23.439 - 0.0000004 * n)
    ra = math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam))
    dec = math.asin(math.sin(eps) * math.sin(lam))
    gmst = (18.697374558 + 24.06570982441908 * n) % 24
    ha = math.radians((gmst * 15 + lon) % 360) - ra
    la = math.radians(lat)
    elev = math.asin(math.sin(la) * math.sin(dec) + math.cos(la) * math.cos(dec) * math.cos(ha))
    az = math.atan2(-math.sin(ha), math.tan(dec) * math.cos(la) - math.sin(la) * math.cos(ha))
    return math.degrees(elev), math.degrees(az) % 360


def moon_position(lat: float, lon: float, t: float | None = None):
    """Moon elevation and azimuth in degrees, the lit fraction of its disc (0 new, 1 full) and
    whether it is waxing. Good to about a degree, which is plenty for drawing it."""
    t = time.time() if t is None else t
    n = t / 86400.0 + 2440587.5 - 2451545.0
    mean = 218.316 + 13.176396 * n
    lam = math.radians(mean + 6.289 * math.sin(math.radians(134.963 + 13.064993 * n)))
    beta = math.radians(5.128 * math.sin(math.radians(93.272 + 13.229350 * n)))
    eps = math.radians(23.439 - 0.0000004 * n)
    ra = math.atan2(math.sin(lam) * math.cos(eps) - math.tan(beta) * math.sin(eps), math.cos(lam))
    dec = math.asin(math.sin(beta) * math.cos(eps) + math.cos(beta) * math.sin(eps) * math.sin(lam))
    gmst = (18.697374558 + 24.06570982441908 * n) % 24
    ha = math.radians((gmst * 15 + lon) % 360) - ra
    la = math.radians(lat)
    elev = math.asin(math.sin(la) * math.sin(dec) + math.cos(la) * math.cos(dec) * math.cos(ha))
    az = math.atan2(-math.sin(ha), math.tan(dec) * math.cos(la) - math.sin(la) * math.cos(ha))
    g = math.radians((357.528 + 0.9856003 * n) % 360)
    sun = math.radians((280.460 + 0.9856474 * n) % 360 + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g))
    apart = (lam - sun) % (2 * math.pi)                     # 0 at new moon, pi at full
    return math.degrees(elev), math.degrees(az) % 360, (1 - math.cos(apart)) / 2, apart < math.pi


@dataclass
class Observer:
    lat: float
    lon: float
    elev_m: float = 0.0
    horizon: float = 5.0                       # lowest elevation angle you can see, degrees
    mask: dict = field(default_factory=dict)   # per-direction overrides, e.g. {"N": 20, "ESE": 12}
    range_m: float = 1.0e9                     # ignore aircraft farther away than this

    def horizon_at(self, az: float) -> float:
        best, best_d = self.horizon, 23.0
        for name, deg in self.mask.items():
            if name in COMPASS:
                d = abs((az - COMPASS.index(name) * 22.5 + 180) % 360 - 180)
                if d < best_d:
                    best, best_d = float(deg), d
        return best


@dataclass
class Sky:
    cloud_low: float = 0.0     # fractions 0..1
    cloud_mid: float = 0.0
    cloud_high: float = 0.0
    cloud_total: float = 0.0
    vis_m: float = 40000.0
    sun_elev: float = 45.0
    sun_az: float = 180.0
    known: bool = False        # False means no weather data, so clear skies are assumed
    moon_elev: float = -90.0   # the moon, for drawing the sky in the plane view
    moon_az: float = 0.0
    moon_lit: float = 0.0      # lit fraction of the disc
    moon_waxing: bool = True
    growing: bool = True       # fields green (growing season) or brown

    def word(self) -> str:
        if not self.known:
            return ""
        if self.vis_m < 1000:
            return "FOG"
        if self.vis_m < 8000:
            return "HAZE"
        c = self.cloud_total
        return "CLEAR" if c < 0.15 else "FEW CLOUDS" if c < 0.5 else "CLOUDY" if c < 0.85 else "OVERCAST"


@dataclass
class Plane:
    """One aircraft as reported by the feed."""
    hex: str
    flight: str = ""
    reg: str = ""
    type: str = ""
    desc: str = ""
    owner: str = ""
    category: str = ""
    military: bool = False
    emergency: bool = False
    lat: float = 0.0
    lon: float = 0.0
    alt_m: float = 0.0     # above mean sea level
    gs: float = 0.0        # ground speed, m/s
    track: float = 0.0     # degrees clockwise from north
    vrate: float = 0.0     # m/s, positive is climbing
    roll: float = 0.0      # degrees, right wing down, when the aircraft reports it
    pos_time: float = 0.0  # unix time the position was valid

    def at(self, t: float):
        """Dead-reckoned (lat, lon, alt) at time t. Feed positions are a few seconds old."""
        dt = max(-5.0, min(600.0, t - self.pos_time))
        d = self.gs * dt
        tr = math.radians(self.track)
        lat = self.lat + math.degrees(d * math.cos(tr) / 6371000.0)
        lon = self.lon + math.degrees(d * math.sin(tr) / (6371000.0 * max(0.05, math.cos(math.radians(self.lat)))))
        return lat, lon, max(0.0, self.alt_m + self.vrate * min(dt, 120.0))

    def span(self) -> float:
        s = SPAN_BY_TYPE.get(self.type) or SPAN_BY_CAT.get(self.category)
        if s:
            return float(s)
        return 34.0 if (self.alt_m > 4500 or self.gs > 130) else 11.0


@dataclass
class Sighting:
    """A plane placed in the observer's sky, with a verdict on whether it can be seen."""
    plane: Plane
    rng: float = 0.0       # slant range, metres
    az: float = 0.0
    elev: float = 0.0
    alt_m: float = 0.0
    score: float = 0.0     # 0..1, product of the visibility factors
    reason: str = ""       # weakest factor: LOW, FAR, HAZE, CLOUD, GLARE
    state: str = "hidden"  # view | faint | hidden
    inbound_s: float | None = None   # seconds until it should come into view
    closing: float = 0.0   # metres per second toward the observer; negative when moving away
    route: dict | None = None

    @property
    def hex(self):
        return self.plane.hex


_frames: dict = {}


def locate(obs: Observer, lat, lon, alt_m):
    """Slant range (m), compass bearing and elevation angle of a point seen from the observer.

    Uses a flat map around the observer with the Earth's curve subtracted from the height.
    Within 150 km that agrees with the exact answer (enu) to about 0.1%, and it needs a
    fraction of the trigonometry, which matters on a Pi Zero doing this hundreds of times a poll.
    """
    key = (obs.lat, obs.lon)
    f = _frames.get(key)
    if f is None:
        la = math.radians(obs.lat)
        w = math.sqrt(1 - _E2 * math.sin(la) ** 2)
        prime, merid = _A / w, _A * (1 - _E2) / w ** 3      # radii of curvature east-west and north-south
        _frames.clear()
        f = _frames[key] = (math.radians(1) * prime, math.radians(1) * merid, 2 * prime)
    # east-west distance per degree shrinks with latitude: use the latitude halfway to the point
    mid = math.radians((lat + obs.lat) * 0.5)
    e = (lon - obs.lon) * f[0] * math.cos(mid)
    n = (lat - obs.lat) * f[1]
    u = alt_m - obs.elev_m - (e * e + n * n) / f[2]
    rng = math.sqrt(e * e + n * n + u * u) or 1.0
    # Meridians converge toward the pole, so the bearing at the observer differs from the
    # bearing on the flat map by half the convergence between the two points.
    az = math.degrees(math.atan2(e, n)) - 0.5 * (lon - obs.lon) * math.sin(mid)
    return rng, az % 360, math.degrees(math.asin(max(-1.0, min(1.0, u / rng))))


def visibility(obs: Observer, sky: Sky, span: float, rng: float, az: float, elev: float, alt_m: float):
    """Score 0..1 for 'could a person standing here pick this out', plus the limiting factor.

    Five independent factors, multiplied:
      LOW    below the local horizon (trees, roofs), per compass direction
      FAR    too small to resolve. By day this is angular size, doubled for
             contrail altitudes. At night it is how far nav lights and strobes carry.
      HAZE   the line of sight runs through more boundary-layer haze than the
             reported visibility allows. High-angle sight lines leave the haze quickly.
      CLOUD  chance of a clear gap through every cloud layer below the aircraft
      GLARE  within a few degrees of the sun
    """
    f = {}
    h = obs.horizon_at(az)
    f["LOW"] = smooth(h - 1.0, h + 2.0, elev)

    arcmin = span / rng * 3437.75
    if alt_m > 8000:
        arcmin *= 1.6                       # a contrail makes it easier to find
    day = smooth(2.5, 6.0, arcmin)
    night = smooth(75000.0, 35000.0, rng)
    w = smooth(-6.0, 0.0, sky.sun_elev)     # 0 at night, 1 in daylight
    f["FAR"] = w * day + (1 - w) * night
    f["FAR"] *= smooth(obs.range_m * 1.04, obs.range_m, rng)    # the range setting, with a soft edge

    haze_path = min(rng, 2000.0 / max(0.03, math.sin(math.radians(max(elev, 0.5)))))
    f["HAZE"] = smooth(1.0, 0.5, haze_path / max(200.0, sky.vis_m))

    agl = alt_m - obs.elev_m
    clear = 1.0
    for base, cover, weight in ((500.0, sky.cloud_low, 1.0), (3000.0, sky.cloud_mid, 1.0),
                                (8000.0, sky.cloud_high, 0.5)):   # cirrus is usually see-through
        if agl > base:
            clear *= 1.0 - min(1.0, cover) * weight
    f["CLOUD"] = clear

    f["GLARE"] = 1.0
    if sky.sun_elev > -1.0:
        e1, e2 = math.radians(elev), math.radians(sky.sun_elev)
        c = (math.sin(e1) * math.sin(e2)
             + math.cos(e1) * math.cos(e2) * math.cos(math.radians(az - sky.sun_az)))
        f["GLARE"] = smooth(6.0, 18.0, math.degrees(math.acos(max(-1.0, min(1.0, c)))))

    score = 1.0
    for v in f.values():
        score *= v
    reason = min(f, key=f.get)
    return score, reason


def assess(obs: Observer, sky: Sky, plane: Plane, t: float) -> Sighting:
    lat, lon, alt = plane.at(t)
    rng, az, elev = locate(obs, lat, lon, alt)
    score, reason = visibility(obs, sky, plane.span(), rng, az, elev, alt)
    return Sighting(plane=plane, rng=rng, az=az, elev=elev, alt_m=alt, score=score, reason=reason)


def inbound_eta(obs: Observer, sky: Sky, plane: Plane, t: float, horizon_s: float = 360.0):
    """Seconds until a currently hidden plane should become visible, or None."""
    if plane.gs < 15:
        return None
    for dt in (45, 90, 150, 240, 360):
        if dt > horizon_s:
            break
        lat, lon, alt = plane.at(t + dt)
        rng, az, elev = locate(obs, lat, lon, alt)
        if visibility(obs, sky, plane.span(), rng, az, elev, alt)[0] >= 0.55:
            return float(dt)
    return None


def route_plausible(route: dict, lat: float, lon: float) -> bool:
    """Callsign-to-route databases are sometimes stale. Reject a route the plane is nowhere near."""
    o, d = route.get("o"), route.get("d")
    if not o or not d:
        return True
    try:
        direct = haversine(o[0], o[1], d[0], d[1])
        via = haversine(o[0], o[1], lat, lon) + haversine(lat, lon, d[0], d[1])
    except (TypeError, ValueError, IndexError):
        return False
    return via <= direct * 1.25 + 150000.0
