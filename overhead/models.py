"""Low-polygon aircraft, generated from a few dimensions per type. There are no model files.

Each aircraft is a small mesh built from its real length, wingspan and fuselage width,
plus a handful of choices: where the wing sits, how far it sweeps, how many engines and
where they hang, what kind of tail. That is enough to tell a 737 from a CRJ from a C-17
from a Cessna at a glance, and it keeps the whole fleet to one table. A typical model is
about 250 points and 200 faces, which a Pi Zero can shade and draw in software.

Axes: x points out of the nose, y out of the right wing, z up. Units are metres.
Faces carry a part name (body, belly, glass, wing, tail, engine, dark, prop) and the
renderer picks the colours, so one mesh can wear any livery.
"""
from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass, field


@dataclass
class Model:
    name: str
    kind: str                 # jet | prop | heli | glider
    length: float
    span: float
    engines: int
    points: list = field(default_factory=list)     # (x, y, z)
    faces: list = field(default_factory=list)      # (point indices, part, solid, (nx, ny, nz))
    spins: list = field(default_factory=list)      # (axis, (cx, cy, cz), point indices): propellers and rotors
    credit: str = "SHAPE GENERATED FROM ITS DIMENSIONS"
    _radius: float = field(default=None, repr=False)
    _lit: list = field(default=None, repr=False)
    _lamps: tuple = field(default=None, repr=False)

    def pts(self, pts):
        base = len(self.points)
        self.points.extend(pts)
        return list(range(base, base + len(pts)))

    def face(self, idx, part, solid=True, away_from=None):
        """Add a polygon (or a 2-point line). `solid` faces are only drawn from the outside.

        With `away_from`, the winding is flipped if needed so the normal points away from
        that point, which is how the tubes get consistent outsides.
        """
        idx = list(idx)
        n = self._normal(idx)
        if away_from is not None and len(idx) >= 3:
            c = [sum(self.points[i][k] for i in idx) / len(idx) for k in range(3)]
            if sum(n[k] * (c[k] - away_from[k]) for k in range(3)) < 0:
                idx.reverse()
                n = (-n[0], -n[1], -n[2])
        self.faces.append((tuple(idx), part, solid, n))

    def poly(self, pts, part, solid=False):
        self.face(self.pts(pts), part, solid)

    def _normal(self, idx):
        if len(idx) < 3:
            return (0.0, 0.0, 1.0)
        nx = ny = nz = 0.0
        p = [self.points[i] for i in idx]
        for a, b in zip(p, p[1:] + p[:1]):          # Newell's method: fine for squashed quads too
            nx += (a[1] - b[1]) * (a[2] + b[2])
            ny += (a[2] - b[2]) * (a[0] + b[0])
            nz += (a[0] - b[0]) * (a[1] + b[1])
        d = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
        return (nx / d, ny / d, nz / d)

    @property
    def radius(self):
        if self._radius is None:
            self._radius = max(math.sqrt(x * x + y * y + z * z) for x, y, z in self.points)
        return self._radius

    @property
    def lit(self):
        """One normal per face for lighting: the face's own, pulled toward its neighbours'.

        Shading every flat face from its true normal shows each polygon as a separate facet.
        Averaging with the faces around it makes a curved skin shade evenly, so a thousand
        triangles read as a smooth fuselage. Neighbours across a hard edge (a wing root, a
        trailing edge) are more than 50 degrees away and are left out, which keeps edges crisp.
        """
        if self._lit is None:
            touching = {}
            for k, (idx, _, _, _) in enumerate(self.faces):
                for i in idx:
                    touching.setdefault(i, []).append(k)
            faces, out = self.faces, []
            for idx, _, _, n in faces:
                if len(idx) < 3:
                    out.append(n)
                    continue
                sx, sy, sz = n
                nx, ny, nz = n
                for i in idx:
                    for k in touching[i]:
                        m = faces[k][3]
                        if nx * m[0] + ny * m[1] + nz * m[2] > 0.64:
                            sx += m[0]
                            sy += m[1]
                            sz += m[2]
                d = math.sqrt(sx * sx + sy * sy + sz * sz) or 1.0
                out.append((sx / d, sy / d, sz / d))
            self._lit = out
        return self._lit

    @property
    def lamps(self):
        """Point indices of the left wingtip, right wingtip and tail, where the navigation lights go."""
        if self._lamps is None:
            p = self.points
            order = range(len(p))
            self._lamps = (min(order, key=lambda i: p[i][1]), max(order, key=lambda i: p[i][1]),
                           min(order, key=lambda i: p[i][0] + 0.3 * abs(p[i][1])))
        return self._lamps


def _ring(cx, cy, cz, r, sides, axis="x", squash=1.0):
    """Points of a circle around the given axis."""
    out = []
    for j in range(sides):
        a = 2 * math.pi * j / sides
        c, s = r * math.cos(a), r * math.sin(a)
        if axis == "x":
            out.append((cx, cy + c, cz + s * squash))
        elif axis == "z":
            out.append((cx + c, cy + s, cz))
        else:
            out.append((cx + c, cy, cz + s))
    return out


def _tube(m, a, b, ca, cb, part):
    """Quads joining two rings. `part` is a name, or a function of the side index."""
    n = len(a)
    mid = tuple((p + q) / 2 for p, q in zip(ca, cb))
    for j in range(n):
        k = (j + 1) % n
        m.face([a[j], b[j], b[k], a[k]], part(j) if callable(part) else part, True, mid)


def _body(m, length, radius, x_nose, nose, tail, sides=8, upsweep=0.7, tall=1.0, z0=0.0, tail_r=0.08,
          windows=False, paint="body"):
    """A tube with a rounded nose and a tapered, upswept tail. Returns z of the tail's centre line."""
    mid = 1 - nose - tail
    ts = [0.0, nose * 0.3, nose * 0.65, nose] + [nose + mid * i / 3 for i in (1, 2, 3)] + \
         [1 - tail * 0.62, 1 - tail * 0.3, 1.0]
    rings, centres = [], []
    zc_tail = z0
    for t in ts:
        zc = z0
        if t < nose:
            r = radius * math.sqrt(max(0.0, 1 - ((nose - t) / nose) ** 2))
        elif t > 1 - tail:
            u = (t - (1 - tail)) / tail
            r = radius * (1 - (1 - tail_r) * u ** 1.3)
            zc = z0 + upsweep * radius * u ** 1.6
            zc_tail = zc
        else:
            r = radius
        x = x_nose - t * length
        rings.append(m.pts(_ring(x, 0.0, zc, max(r, 0.001), sides, "x", tall)))
        centres.append((x, 0.0, zc))
    belly = (sides * 5 // 8, sides * 6 // 8)             # the two faces underneath
    top = (sides // 8, sides * 2 // 8)                   # the two faces on top
    for i in range(len(rings) - 1):
        def part(j, i=i):
            if i in (1, 2) and j in top:
                return "glass"                           # windscreen
            return "belly" if j in belly else paint
        _tube(m, rings[i], rings[i + 1], centres[i], centres[i + 1], part)
        if windows and 3 <= i <= 6:                      # a dark line along each side for the cabin windows
            for j in (0, sides * 3 // 8):
                k = j + 1
                def lift(p, q, cz=centres[i][2]):
                    y, z = p[1] * 0.6 + q[1] * 0.4, p[2] * 0.6 + q[2] * 0.4
                    return (p[0], y * 1.03, cz + (z - cz) * 1.03)
                a0, a1 = m.points[rings[i][j]], m.points[rings[i][k]]
                b0, b1 = m.points[rings[i + 1][j]], m.points[rings[i + 1][k]]
                m.poly([lift(a0, a1), lift(b0, b1)], "glass")
    m.face(rings[-1], paint, True, centres[-2])          # close the tail
    return zc_tail


def _panel(m, root, tip, root_chord, tip_chord, part):
    """A flat lifting surface between two leading-edge points. Chord runs back along x."""
    rte = (root[0] - root_chord, root[1], root[2])
    tte = (tip[0] - tip_chord, tip[1], tip[2])
    m.poly([root, tip, tte, rte], part)


def _pod(m, x, y, z, r, length, sides=6, part="engine"):
    front, back = m.pts(_ring(x + length / 2, y, z, r, sides)), m.pts(_ring(x - length / 2, y, z, r * 0.8, sides))
    _tube(m, front, back, (x + length / 2, y, z), (x - length / 2, y, z), part)
    m.face(front, "dark", True, (x, y, z))               # intake
    m.face(back, "dark", True, (x, y, z))                # exhaust


def _blades(m, cx, cy, cz, r, axis, count=2, width=0.12):
    """Thin blades through a hub, registered to spin."""
    idx = []
    for b in range(count):
        a = math.pi * b / count
        c, s = math.cos(a), math.sin(a)
        w = r * width
        if axis == "x":         # propeller: blades in the y-z plane
            quad = [(cx, cy + c * r - s * w, cz + s * r + c * w), (cx, cy + c * r + s * w, cz + s * r - c * w),
                    (cx, cy - c * r + s * w, cz - s * r - c * w), (cx, cy - c * r - s * w, cz - s * r + c * w)]
        elif axis == "z":       # main rotor: blades in the x-y plane
            quad = [(cx + c * r - s * w, cy + s * r + c * w, cz), (cx + c * r + s * w, cy + s * r - c * w, cz),
                    (cx - c * r + s * w, cy - s * r - c * w, cz), (cx - c * r - s * w, cy - s * r + c * w, cz)]
        else:                   # tail rotor: blades in the x-z plane
            quad = [(cx + c * r - s * w, cy, cz + s * r + c * w), (cx + c * r + s * w, cy, cz + s * r - c * w),
                    (cx - c * r + s * w, cy, cz - s * r - c * w), (cx - c * r - s * w, cy, cz - s * r + c * w)]
        pts = m.pts(quad)
        m.face(pts, "prop", False)
        idx += pts
    m.spins.append((axis, (cx, cy, cz), idx))


def airplane(name, length, span, radius, *, kind="jet", wing="low", sweep=25.0, engines=2, mount="wing",
             tail="low", winglets=False, tall=1.0, fins=1, dome=False, chord=None):
    """Fixed-wing aircraft. `mount` is wing, rear, nose or none; `tail` is low or T."""
    m = Model(name, kind, length, span, engines)
    nose = min(0.24, 3.2 * radius / length)
    tailf = min(0.42, 6.5 * radius / length)
    x_nose = length / 2
    z_tail = _body(m, length, radius, x_nose, nose, tailf, tall=tall, windows=radius >= 1.1)

    props = kind == "prop"
    tsweep = math.tan(math.radians(sweep))
    root_chord = chord or span * (0.2 if sweep >= 15 else 0.15)
    tip_chord = root_chord * (0.28 if sweep >= 15 else 0.6)
    y_root = radius * 0.85
    wing_z = {"low": -radius * 0.55, "mid": 0.0, "high": radius * 0.78}[wing] * tall
    dihedral = math.tan(math.radians({"low": 5.0, "mid": 0.0, "high": -1.5}[wing]))
    start = 0.44 if mount == "rear" else 0.33 if sweep >= 15 else 0.27      # leading edge, fraction back from the nose
    wing_x = x_nose - start * length
    half = span / 2 - y_root

    def le(frac):       # leading-edge point of the right wing at a fraction of the half span
        return (wing_x - half * frac * tsweep, y_root + half * frac, wing_z + half * frac * dihedral)

    for side in (1, -1):
        tip = le(1.0)
        _panel(m, (wing_x, side * y_root * 0.2, wing_z), (tip[0], side * tip[1], tip[2]), root_chord, tip_chord, "wing")
        if winglets:
            h = span * 0.035
            m.poly([(tip[0], side * tip[1], tip[2]), (tip[0] - tip_chord * 0.4, side * (tip[1] + h * 0.25), tip[2] + h),
                    (tip[0] - tip_chord, side * (tip[1] + h * 0.25), tip[2] + h), (tip[0] - tip_chord, side * tip[1], tip[2])], "tail")

    # fin and tailplane
    fin_h = min(0.2 * span, 0.24 * length) * (0.8 if fins == 2 else 1.0)
    fin_root = fin_h * 0.95
    fin_sweep = math.tan(math.radians(38.0 if sweep >= 15 else 18.0))
    fin_x = -x_nose + fin_root + 0.015 * length
    fin_z = z_tail + radius * 0.15
    for dy in ((0.0,) if fins == 1 else (radius * 0.75, -radius * 0.75)):
        lean = 0.25 * (1 if dy > 0 else -1 if dy < 0 else 0)
        _panel(m, (fin_x, dy, fin_z), (fin_x - fin_h * fin_sweep, dy + lean * fin_h, fin_z + fin_h),
               fin_root, fin_root * 0.42, "tail")
    stab_span = span * (0.36 if not props else 0.38)
    stab_chord = root_chord * 0.55
    if tail == "T":
        sx, sz = fin_x - fin_h * fin_sweep + 0.1 * stab_chord, fin_z + fin_h
    else:
        sx, sz = fin_x + 0.1 * stab_chord, z_tail * 0.6
    for side in (1, -1):
        hs = stab_span / 2
        _panel(m, (sx, 0.0, sz), (sx - hs * tsweep * 0.9, side * stab_span / 2, sz + hs * 0.05),
               stab_chord, stab_chord * 0.45, "wing")

    # engines
    pod_r = max(0.3, radius * (0.5 if radius > 2.4 else 0.42))
    if mount == "wing":
        spots = {1: [], 2: [0.34], 4: [0.3, 0.6], 6: [0.26, 0.48, 0.7], 8: [0.3, 0.6]}.get(engines, [0.34])
        for frac in spots:
            lx, ly, lz = le(frac)
            for side in (1, -1):
                if props:
                    nl = root_chord * 0.9
                    _pod(m, lx + nl * 0.15, side * ly, lz + pod_r * 0.1, pod_r * 0.7, nl)
                    _blades(m, lx + nl * 0.68, side * ly, lz + pod_r * 0.1, span * 0.068, "x")
                else:
                    pl = pod_r * 3.4
                    under = lz - pod_r * (1.5 if wing == "high" else 1.3)
                    _pod(m, lx + pl * 0.3, side * ly, under, pod_r, pl)
                    m.poly([(lx - root_chord * 0.1, side * ly, lz), (lx + pl * 0.1, side * ly, under + pod_r)], "dark")
    elif mount == "rear":
        pl = pod_r * 3.0
        ex = -x_nose + (tailf + 0.02) * length
        for side in (1, -1):
            _pod(m, ex, side * (radius + pod_r * 1.05), radius * 0.3, pod_r, pl)
    elif mount == "nose" and props:
        _blades(m, x_nose + 0.08, 0.0, 0.0, span * 0.085, "x")
    if engines == 3:                    # third engine at the base of the fin
        _pod(m, fin_x - fin_root * 0.3, 0.0, fin_z + pod_r * 0.6, pod_r, pod_r * 3.2)

    if dome:                            # radar dish on two legs, as on an E-3
        dz, dx = radius + span * 0.07, -0.12 * length
        m.poly(_ring(dx, 0.0, dz, span * 0.1, 12, "z"), "dark")
        m.poly([(dx, radius * 0.5, radius * 0.8), (dx, radius * 0.5, dz)], "dark")
        m.poly([(dx, -radius * 0.5, radius * 0.8), (dx, -radius * 0.5, dz)], "dark")
    return m


def helicopter(name, length, rotor, radius, *, tandem=False, engines=2, skids=True):
    m = Model(name, "heli", length, rotor, engines)
    if tandem:                          # two main rotors, one long cabin (CH-47)
        body = length * 0.52
        _body(m, body, radius, body / 2, 0.2, 0.22, upsweep=0.5, windows=True)
        for x, lift in ((body * 0.42, 0.0), (-body * 0.42, 0.6 * radius)):
            top = radius * 1.9 + lift
            m.poly([(x, 0.0, radius), (x, 0.0, top)], "dark")
            _blades(m, x, 0.0, top, rotor / 2, "z", 2, 0.05)
        return m
    cabin = length * 0.42
    x_nose = length * 0.36
    _body(m, cabin, radius, x_nose, 0.32, 0.38, upsweep=0.35, tail_r=0.22)
    # tail boom
    bx0, bx1 = x_nose - cabin * 0.92, x_nose - length * 0.92
    a, b = m.pts(_ring(bx0, 0.0, radius * 0.35, radius * 0.22, 4)), m.pts(_ring(bx1, 0.0, radius * 0.75, radius * 0.11, 4))
    _tube(m, a, b, (bx0, 0.0, radius * 0.35), (bx1, 0.0, radius * 0.75), "body")
    # fin and tail rotor
    m.poly([(bx1 + radius * 0.5, 0.0, radius * 0.7), (bx1 - radius * 0.2, 0.0, radius * 1.9),
            (bx1 - radius * 0.6, 0.0, radius * 1.9), (bx1 - radius * 0.3, 0.0, radius * 0.7)], "tail")
    _blades(m, bx1 - radius * 0.2, radius * 0.3, radius * 1.35, rotor * 0.09, "y", 2, 0.16)
    # main rotor
    cx, top = x_nose - cabin * 0.52, radius * 1.75
    m.poly([(cx, 0.0, radius * 0.95), (cx, 0.0, top)], "dark")
    _blades(m, cx, 0.0, top, rotor / 2, "z", 2, 0.05)
    if skids:
        for side in (1, -1):
            y, z = side * radius * 0.85, -radius * 1.35
            m.poly([(cx + cabin * 0.42, y, z + radius * 0.15), (cx + cabin * 0.36, y, z)], "dark")
            m.poly([(cx + cabin * 0.36, y, z), (cx - cabin * 0.38, y, z)], "dark")
            m.poly([(cx + cabin * 0.2, y * 0.8, -radius * 0.8), (cx + cabin * 0.2, y, z)], "dark")
            m.poly([(cx - cabin * 0.2, y * 0.8, -radius * 0.8), (cx - cabin * 0.2, y, z)], "dark")
    return m


# name, length, span, fuselage radius, then options. Dimensions are the real ones, rounded.
def _nb(name, length, span=35.8, r=1.9, **kw):          # narrow-body twin jet
    return ("A", name, length, span, r, dict(dict(sweep=26, winglets=True), **kw))


def _wb(name, length, span, r, **kw):                   # wide-body
    return ("A", name, length, span, r, dict(dict(sweep=31), **kw))


def _rj(name, length, span, r, **kw):                   # rear-engined, T-tail
    return ("A", name, length, span, r, dict(dict(sweep=26, mount="rear", tail="T"), **kw))


def _tp(name, length, span, r, **kw):                   # turboprop or piston
    return ("A", name, length, span, r, dict(dict(kind="prop", sweep=3), **kw))


def _he(name, length, rotor, r, **kw):
    return ("H", name, length, rotor, r, kw)


TYPES = {
    # Airbus narrow-bodies
    "A318": _nb("AIRBUS A318", 31.4, 34.1, 1.98), "A319": _nb("AIRBUS A319", 33.8, 35.8, 1.98),
    "A320": _nb("AIRBUS A320", 37.6, 35.8, 1.98), "A321": _nb("AIRBUS A321", 44.5, 35.8, 1.98),
    "A19N": _nb("AIRBUS A319NEO", 33.8, 35.8, 1.98), "A20N": _nb("AIRBUS A320NEO", 37.6, 35.8, 1.98),
    "A21N": _nb("AIRBUS A321NEO", 44.5, 35.8, 1.98),
    "BCS1": _nb("AIRBUS A220-100", 35.0, 35.1, 1.75), "BCS3": _nb("AIRBUS A220-300", 38.7, 35.1, 1.75),
    # Boeing narrow-bodies
    "B712": _rj("BOEING 717", 37.8, 28.4, 1.67), "B737": _nb("BOEING 737-700", 33.6), "B738": _nb("BOEING 737-800", 39.5),
    "B739": _nb("BOEING 737-900", 42.1), "B37M": _nb("BOEING 737 MAX 7", 35.6), "B38M": _nb("BOEING 737 MAX 8", 39.5),
    "B39M": _nb("BOEING 737 MAX 9", 42.2), "B3XM": _nb("BOEING 737 MAX 10", 43.8),
    "B733": _nb("BOEING 737-300", 33.4, 28.9, winglets=False), "B734": _nb("BOEING 737-400", 36.4, 28.9, winglets=False),
    "B735": _nb("BOEING 737-500", 31.0, 28.9, winglets=False),
    "B752": _nb("BOEING 757-200", 47.3, 38.0), "B753": _nb("BOEING 757-300", 54.4, 38.0),
    # wide-bodies
    "B762": _wb("BOEING 767-200", 48.5, 47.6, 2.5), "B763": _wb("BOEING 767-300", 54.9, 47.6, 2.5),
    "B764": _wb("BOEING 767-400", 61.4, 51.9, 2.5),
    "B772": _wb("BOEING 777-200", 63.7, 60.9, 3.1), "B77L": _wb("BOEING 777-200LR", 63.7, 64.8, 3.1),
    "B773": _wb("BOEING 777-300", 73.9, 60.9, 3.1), "B77W": _wb("BOEING 777-300ER", 73.9, 64.8, 3.1),
    "B788": _wb("BOEING 787-8", 56.7, 60.1, 2.9), "B789": _wb("BOEING 787-9", 62.8, 60.1, 2.9),
    "B78X": _wb("BOEING 787-10", 68.3, 60.1, 2.9),
    "A306": _wb("AIRBUS A300-600", 54.1, 44.8, 2.82), "A310": _wb("AIRBUS A310", 46.7, 43.9, 2.82),
    "A332": _wb("AIRBUS A330-200", 58.8, 60.3, 2.82), "A333": _wb("AIRBUS A330-300", 63.7, 60.3, 2.82),
    "A338": _wb("AIRBUS A330-800", 58.8, 64.0, 2.82), "A339": _wb("AIRBUS A330-900", 63.7, 64.0, 2.82),
    "A359": _wb("AIRBUS A350-900", 66.8, 64.8, 2.98, winglets=True), "A35K": _wb("AIRBUS A350-1000", 73.8, 64.8, 2.98, winglets=True),
    "A342": _wb("AIRBUS A340-200", 59.4, 60.3, 2.82, engines=4), "A343": _wb("AIRBUS A340-300", 63.7, 60.3, 2.82, engines=4),
    "A346": _wb("AIRBUS A340-600", 75.4, 63.5, 2.82, engines=4),
    "B744": _wb("BOEING 747-400", 70.7, 64.4, 3.25, engines=4, sweep=37), "B748": _wb("BOEING 747-8", 76.3, 68.4, 3.25, engines=4, sweep=37),
    "A388": _wb("AIRBUS A380-800", 72.7, 79.8, 3.57, engines=4, tall=1.2, sweep=33),
    "MD11": _wb("MCDONNELL DOUGLAS MD-11", 61.6, 51.7, 3.0, engines=3, sweep=35, winglets=True),
    "DC10": _wb("MCDONNELL DOUGLAS DC-10", 55.5, 50.4, 3.0, engines=3, sweep=35),
    # regional jets
    "CRJ2": _rj("BOMBARDIER CRJ-200", 26.8, 21.2, 1.35), "CRJ7": _rj("BOMBARDIER CRJ-700", 32.5, 23.2, 1.35, winglets=True),
    "CRJ9": _rj("BOMBARDIER CRJ-900", 36.2, 24.9, 1.35, winglets=True), "CRJX": _rj("BOMBARDIER CRJ-1000", 39.1, 26.2, 1.35, winglets=True),
    "E135": _rj("EMBRAER ERJ-135", 26.3, 20.0, 1.14), "E145": _rj("EMBRAER ERJ-145", 29.9, 20.0, 1.14),
    "E45X": _rj("EMBRAER ERJ-145XR", 29.9, 21.0, 1.14, winglets=True),
    "MD82": _rj("MCDONNELL DOUGLAS MD-82", 45.0, 32.9, 1.67), "MD83": _rj("MCDONNELL DOUGLAS MD-83", 45.0, 32.9, 1.67),
    "MD88": _rj("MCDONNELL DOUGLAS MD-88", 45.0, 32.9, 1.67), "MD90": _rj("MCDONNELL DOUGLAS MD-90", 46.5, 32.9, 1.67),
    "E170": _nb("EMBRAER 170", 29.9, 26.0, 1.5), "E75L": _nb("EMBRAER 175", 31.7, 28.7, 1.5), "E75S": _nb("EMBRAER 175", 31.7, 26.0, 1.5),
    "E190": _nb("EMBRAER 190", 36.2, 28.7, 1.5), "E195": _nb("EMBRAER 195", 38.7, 28.7, 1.5),
    "E290": _nb("EMBRAER E190-E2", 36.2, 33.7, 1.5), "E295": _nb("EMBRAER E195-E2", 41.5, 35.1, 1.5),
    # business jets
    "C25A": _rj("CESSNA CITATION CJ2", 14.5, 15.2, 0.8, sweep=8), "C25B": _rj("CESSNA CITATION CJ3", 15.6, 16.3, 0.8, sweep=8),
    "C25C": _rj("CESSNA CITATION CJ4", 16.3, 15.5, 0.8, sweep=12), "C525": _rj("CESSNA CITATIONJET", 13.0, 14.3, 0.8, sweep=5),
    "C510": _rj("CESSNA CITATION MUSTANG", 12.4, 13.2, 0.75, sweep=8), "C560": _rj("CESSNA CITATION V", 14.9, 15.9, 0.85, sweep=5, tail="low"),
    "C56X": _rj("CESSNA CITATION EXCEL", 16.0, 17.0, 0.9, sweep=5, tail="low"), "C680": _rj("CESSNA CITATION SOVEREIGN", 19.4, 19.3, 0.9, sweep=16),
    "C68A": _rj("CESSNA CITATION LATITUDE", 19.0, 22.1, 1.0, sweep=16), "C700": _rj("CESSNA CITATION LONGITUDE", 22.3, 21.0, 1.0, winglets=True),
    "C750": _rj("CESSNA CITATION X", 22.0, 19.4, 0.9, sweep=37), "CL30": _rj("BOMBARDIER CHALLENGER 300", 20.9, 19.5, 1.1, winglets=True),
    "CL35": _rj("BOMBARDIER CHALLENGER 350", 20.9, 21.0, 1.1, winglets=True), "CL60": _rj("BOMBARDIER CHALLENGER 600", 20.9, 19.6, 1.35, winglets=True),
    "GLF4": _rj("GULFSTREAM IV", 26.9, 23.7, 1.2, winglets=True), "GLF5": _rj("GULFSTREAM V", 29.4, 28.5, 1.2, winglets=True),
    "GLF6": _rj("GULFSTREAM G650", 30.4, 30.4, 1.3, winglets=True), "GLEX": _rj("BOMBARDIER GLOBAL EXPRESS", 30.3, 28.7, 1.35, winglets=True),
    "GL5T": _rj("BOMBARDIER GLOBAL 5000", 29.5, 28.7, 1.35, winglets=True), "GL7T": _rj("BOMBARDIER GLOBAL 7500", 33.8, 31.7, 1.35, winglets=True),
    "LJ35": _rj("LEARJET 35", 14.8, 12.0, 0.8, sweep=13), "LJ45": _rj("LEARJET 45", 17.7, 14.6, 0.8, sweep=13, winglets=True),
    "LJ60": _rj("LEARJET 60", 17.9, 13.3, 0.9, sweep=13, winglets=True), "LJ75": _rj("LEARJET 75", 17.7, 15.5, 0.8, sweep=13, winglets=True),
    "E50P": _rj("EMBRAER PHENOM 100", 12.8, 12.3, 0.8, sweep=10), "E55P": _rj("EMBRAER PHENOM 300", 15.6, 16.2, 0.85, sweep=18, winglets=True),
    "E545": _rj("EMBRAER LEGACY 450", 19.7, 20.3, 1.1, winglets=True), "E550": _rj("EMBRAER PRAETOR 600", 20.7, 21.5, 1.1, winglets=True),
    "F2TH": _rj("DASSAULT FALCON 2000", 20.2, 19.3, 1.2, tail="low"), "F900": _rj("DASSAULT FALCON 900", 20.2, 19.3, 1.2, engines=3, tail="low"),
    "FA7X": _rj("DASSAULT FALCON 7X", 23.4, 26.2, 1.2, engines=3, tail="low", winglets=True),
    "H25B": _rj("HAWKER 800", 15.6, 15.7, 0.95, sweep=20), "BE40": _rj("BEECHJET 400", 14.8, 13.3, 0.85, sweep=20),
    "PC24": _rj("PILATUS PC-24", 16.8, 17.0, 0.9, sweep=10), "HDJT": _rj("HONDAJET", 13.0, 12.1, 0.8, sweep=8),
    "SF50": ("A", "CIRRUS VISION JET", 9.4, 11.8, 0.75, dict(sweep=3, engines=1, mount="none", fins=2)),
    # turboprops
    "DH8A": _tp("DE HAVILLAND DASH 8-100", 22.3, 25.9, 1.35, wing="high", tail="T"), "DH8B": _tp("DE HAVILLAND DASH 8-200", 22.3, 25.9, 1.35, wing="high", tail="T"),
    "DH8C": _tp("DE HAVILLAND DASH 8-300", 25.7, 27.4, 1.35, wing="high", tail="T"), "DH8D": _tp("DE HAVILLAND DASH 8-400", 32.8, 28.4, 1.35, wing="high", tail="T"),
    "AT43": _tp("ATR 42", 22.7, 24.6, 1.3, wing="high", tail="T"), "AT45": _tp("ATR 42-500", 22.7, 24.6, 1.3, wing="high", tail="T"),
    "AT72": _tp("ATR 72", 27.2, 27.1, 1.3, wing="high", tail="T"), "AT75": _tp("ATR 72-500", 27.2, 27.1, 1.3, wing="high", tail="T"),
    "AT76": _tp("ATR 72-600", 27.2, 27.1, 1.3, wing="high", tail="T"),
    "SF34": _tp("SAAB 340", 19.7, 21.4, 1.15), "E120": _tp("EMBRAER BRASILIA", 20.0, 19.8, 1.15, tail="T"),
    "B190": _tp("BEECHCRAFT 1900", 17.6, 17.7, 0.8, tail="T"), "BE20": _tp("BEECHCRAFT KING AIR 200", 13.3, 16.6, 0.75, tail="T"),
    "B350": _tp("BEECHCRAFT KING AIR 350", 14.2, 17.7, 0.75, tail="T", winglets=True), "BE9L": _tp("BEECHCRAFT KING AIR 90", 10.8, 15.3, 0.75),
    "PC12": _tp("PILATUS PC-12", 14.4, 16.3, 0.8, engines=1, mount="nose", tail="T", winglets=True),
    "C208": _tp("CESSNA CARAVAN", 11.5, 15.9, 0.8, engines=1, mount="nose", wing="high"),
    "TBM7": _tp("SOCATA TBM 700", 10.6, 12.7, 0.65, engines=1, mount="nose"), "TBM8": _tp("SOCATA TBM 850", 10.6, 12.7, 0.65, engines=1, mount="nose"),
    "TBM9": _tp("DAHER TBM 900", 10.7, 12.8, 0.65, engines=1, mount="nose", winglets=True),
    "P46T": _tp("PIPER MERIDIAN", 9.0, 13.1, 0.62, engines=1, mount="nose"),
    # piston singles and twins
    "C150": _tp("CESSNA 150", 7.3, 10.2, 0.55, engines=1, mount="nose", wing="high"), "C152": _tp("CESSNA 152", 7.3, 10.2, 0.55, engines=1, mount="nose", wing="high"),
    "C172": _tp("CESSNA 172 SKYHAWK", 8.3, 11.0, 0.6, engines=1, mount="nose", wing="high"), "C177": _tp("CESSNA CARDINAL", 8.4, 10.8, 0.6, engines=1, mount="nose", wing="high"),
    "C182": _tp("CESSNA 182 SKYLANE", 8.8, 11.0, 0.62, engines=1, mount="nose", wing="high"), "C206": _tp("CESSNA 206", 8.6, 11.0, 0.65, engines=1, mount="nose", wing="high"),
    "C210": _tp("CESSNA 210", 8.6, 11.2, 0.62, engines=1, mount="nose", wing="high"),
    "P28A": _tp("PIPER CHEROKEE", 7.3, 10.7, 0.55, engines=1, mount="nose"), "P28B": _tp("PIPER DAKOTA", 7.5, 10.7, 0.55, engines=1, mount="nose"),
    "P28R": _tp("PIPER ARROW", 7.5, 10.8, 0.55, engines=1, mount="nose"), "PA32": _tp("PIPER CHEROKEE SIX", 8.4, 11.0, 0.6, engines=1, mount="nose"),
    "P32R": _tp("PIPER SARATOGA", 8.4, 11.0, 0.6, engines=1, mount="nose"), "PA46": _tp("PIPER MALIBU", 8.7, 13.1, 0.62, engines=1, mount="nose"),
    "SR20": _tp("CIRRUS SR20", 7.9, 11.7, 0.6, engines=1, mount="nose"), "SR22": _tp("CIRRUS SR22", 7.9, 11.7, 0.6, engines=1, mount="nose"),
    "S22T": _tp("CIRRUS SR22T", 7.9, 11.7, 0.6, engines=1, mount="nose"),
    "DA40": _tp("DIAMOND DA40", 8.1, 11.9, 0.55, engines=1, mount="nose", tail="T"), "DA42": _tp("DIAMOND DA42", 8.6, 13.4, 0.6, tail="T"),
    "BE35": _tp("BEECHCRAFT BONANZA", 8.0, 10.2, 0.6, engines=1, mount="nose"), "BE36": _tp("BEECHCRAFT BONANZA 36", 8.4, 10.2, 0.6, engines=1, mount="nose"),
    "BE33": _tp("BEECHCRAFT DEBONAIR", 7.8, 10.2, 0.6, engines=1, mount="nose"), "M20P": _tp("MOONEY M20", 8.2, 11.0, 0.55, engines=1, mount="nose"),
    "BE55": _tp("BEECHCRAFT BARON 55", 8.5, 11.5, 0.6), "BE58": _tp("BEECHCRAFT BARON 58", 9.1, 11.5, 0.6),
    "PA34": _tp("PIPER SENECA", 8.7, 11.9, 0.6), "PA44": _tp("PIPER SEMINOLE", 8.4, 11.8, 0.58, tail="T"), "C310": _tp("CESSNA 310", 9.7, 11.3, 0.62),
    "C340": _tp("CESSNA 340", 10.5, 11.6, 0.65), "C414": _tp("CESSNA 414", 11.1, 13.5, 0.7), "C421": _tp("CESSNA 421", 11.1, 12.5, 0.7),
    "PA31": _tp("PIPER NAVAJO", 9.9, 12.4, 0.7),
    # military
    "C17": ("A", "BOEING C-17 GLOBEMASTER III", 53.0, 51.7, 3.4, dict(wing="high", tail="T", engines=4, sweep=25, winglets=True)),
    "C5M": ("A", "LOCKHEED C-5M GALAXY", 75.3, 67.9, 3.6, dict(wing="high", tail="T", engines=4, sweep=25)),
    "C130": _tp("LOCKHEED C-130 HERCULES", 29.8, 40.4, 2.2, wing="high", engines=4), "C30J": _tp("LOCKHEED C-130J", 29.8, 40.4, 2.2, wing="high", engines=4),
    "K35R": _nb("BOEING KC-135R", 41.5, 39.9, 1.88, engines=4, sweep=35, winglets=False), "K35E": _nb("BOEING KC-135E", 41.5, 39.9, 1.88, engines=4, sweep=35, winglets=False),
    "KC46": _wb("BOEING KC-46 PEGASUS", 50.5, 48.1, 2.5), "KC10": _wb("MCDONNELL DOUGLAS KC-10", 55.4, 50.4, 3.0, engines=3, sweep=35),
    "E3TF": _nb("BOEING E-3 SENTRY", 46.6, 44.4, 1.88, engines=4, sweep=35, winglets=False, dome=True),
    "E3CF": _nb("BOEING E-3 SENTRY", 46.6, 44.4, 1.88, engines=4, sweep=35, winglets=False, dome=True),
    "E6": _nb("BOEING E-6 MERCURY", 45.8, 45.2, 1.88, engines=4, sweep=35, winglets=False),
    "P8": _nb("BOEING P-8 POSEIDON", 39.5, 37.6, 1.88), "C40": _nb("BOEING C-40", 33.6, 35.8, 1.88), "C32": _nb("BOEING C-32", 47.3, 38.0, 1.88),
    "B52": ("A", "BOEING B-52", 48.5, 56.4, 1.6, dict(wing="high", engines=8, sweep=35)),
    "C2": _tp("GRUMMAN C-2 GREYHOUND", 17.3, 24.6, 1.3, wing="high", fins=2), "E2": _tp("NORTHROP GRUMMAN E-2 HAWKEYE", 17.6, 24.6, 1.1, wing="high", fins=2, dome=True),
    "P3": _tp("LOCKHEED P-3 ORION", 35.6, 30.4, 1.7, engines=4), "T6": _tp("BEECHCRAFT T-6 TEXAN II", 10.2, 10.2, 0.55, engines=1, mount="nose"),
    "TEX2": _tp("BEECHCRAFT T-6 TEXAN II", 10.2, 10.2, 0.55, engines=1, mount="nose"),
    "V22": _tp("BELL BOEING V-22 OSPREY", 17.5, 14.0, 1.2, wing="high", fins=2),
    "F16": ("A", "GENERAL DYNAMICS F-16", 15.0, 9.9, 0.75, dict(wing="mid", sweep=40, engines=1, mount="none")),
    "F15": ("A", "MCDONNELL DOUGLAS F-15", 19.4, 13.0, 0.95, dict(wing="high", sweep=45, engines=2, mount="none", fins=2)),
    "F18": ("A", "BOEING F/A-18", 17.1, 12.3, 0.85, dict(wing="mid", sweep=27, engines=2, mount="none", fins=2)),
    "F18H": ("A", "BOEING F/A-18", 17.1, 12.3, 0.85, dict(wing="mid", sweep=27, engines=2, mount="none", fins=2)),
    "F18S": ("A", "BOEING F/A-18 SUPER HORNET", 18.3, 13.6, 0.9, dict(wing="mid", sweep=27, engines=2, mount="none", fins=2)),
    "F22": ("A", "LOCKHEED MARTIN F-22", 18.9, 13.6, 1.0, dict(wing="mid", sweep=42, engines=2, mount="none", fins=2)),
    "F35": ("A", "LOCKHEED MARTIN F-35", 15.7, 10.7, 0.95, dict(wing="mid", sweep=35, engines=1, mount="none", fins=2)),
    "A10": ("A", "FAIRCHILD A-10", 16.3, 17.5, 0.8, dict(sweep=3, mount="rear", fins=2, tail="low")),
    "T38": ("A", "NORTHROP T-38 TALON", 14.1, 7.7, 0.55, dict(sweep=32, engines=2, mount="none")),
    # helicopters
    "H60": _he("SIKORSKY UH-60 BLACK HAWK", 19.8, 16.4, 1.15, skids=False), "S70": _he("SIKORSKY S-70", 19.8, 16.4, 1.15, skids=False),
    "H47": _he("BOEING CH-47 CHINOOK", 30.1, 18.3, 1.5, tandem=True), "H64": _he("BOEING AH-64 APACHE", 17.7, 14.6, 0.75, skids=False),
    "H53": _he("SIKORSKY CH-53", 30.2, 24.1, 1.5, skids=False, engines=3), "UH1": _he("BELL UH-1 HUEY", 17.4, 14.6, 1.05, engines=1),
    "UH1Y": _he("BELL UH-1Y VENOM", 17.8, 14.9, 1.05), "EC35": _he("AIRBUS H135", 12.2, 10.2, 0.85), "EC45": _he("AIRBUS H145", 13.0, 11.0, 0.9),
    "EC30": _he("AIRBUS H130", 12.6, 10.7, 0.9, engines=1), "EC20": _he("AIRBUS H120", 11.5, 10.0, 0.8, engines=1),
    "AS50": _he("AIRBUS H125", 12.9, 10.7, 0.85, engines=1), "AS65": _he("AIRBUS DAUPHIN", 13.7, 11.9, 0.95, skids=False),
    "B06": _he("BELL 206 JETRANGER", 11.9, 10.2, 0.7, engines=1), "B407": _he("BELL 407", 12.7, 10.7, 0.75, engines=1),
    "B412": _he("BELL 412", 17.1, 14.0, 1.0), "B429": _he("BELL 429", 13.0, 11.0, 0.9), "B505": _he("BELL 505", 12.9, 11.3, 0.75, engines=1),
    "R22": _he("ROBINSON R22", 8.8, 7.7, 0.55, engines=1), "R44": _he("ROBINSON R44", 11.7, 10.1, 0.65, engines=1), "R66": _he("ROBINSON R66", 11.7, 10.1, 0.65, engines=1),
    "A109": _he("AGUSTA A109", 13.0, 11.0, 0.8, skids=False), "A139": _he("LEONARDO AW139", 16.7, 13.8, 1.1, skids=False),
    "A169": _he("LEONARDO AW169", 14.7, 12.1, 1.0, skids=False), "S76": _he("SIKORSKY S-76", 16.0, 13.4, 1.0, skids=False),
    "S92": _he("SIKORSKY S-92", 20.9, 17.2, 1.3, skids=False), "MD52": _he("MD 520N", 9.8, 8.3, 0.65, engines=1), "H500": _he("MD 500", 9.4, 8.0, 0.65, engines=1),
    # gliders
    "GLID": ("A", "GLIDER", 7.0, 17.0, 0.35, dict(kind="glider", sweep=1, engines=0, mount="none", tail="T", wing="mid", chord=0.9)),
}

# Types not in the table fall back to a close relative by the shape of their code.
FAMILIES = [
    (r"^A3[12]|^A2[01]N", "A320"), (r"^B73|^B3.M", "B738"), (r"^B75", "B752"), (r"^B76", "B763"), (r"^B77", "B772"),
    (r"^B78", "B789"), (r"^B74", "B744"), (r"^A33", "A333"), (r"^A35", "A359"), (r"^A34", "A343"), (r"^A38", "A388"),
    (r"^CRJ", "CRJ7"), (r"^E1[34]", "E145"), (r"^E1[79]|^E7|^E29", "E75L"), (r"^MD[89]", "MD83"), (r"^DH8", "DH8D"), (r"^AT[47]", "AT76"),
    (r"^C25|^C5[0-9]{2}$", "C25B"), (r"^C6[0-9]|^C7[05]", "C680"), (r"^GL[F5-7E]", "GLF5"), (r"^LJ", "LJ45"), (r"^CL[36]", "CL35"),
    (r"^F9|^FA|^F2", "F2TH"), (r"^E5", "E55P"), (r"^C1[5-8]|^C20[5-9]|^C21", "C172"), (r"^P28|^PA2|^PA32|^P32", "P28A"),
    (r"^SR2|^S22", "SR22"), (r"^BE3|^M20", "BE36"), (r"^BE5|^PA3[04]|^PA44|^C3[0-9]|^C4[0-2]", "BE58"), (r"^BE[29]|^B35|^B19", "B350"),
    (r"^TBM|^PC12|^P46", "PC12"), (r"^C13|^C30", "C130"), (r"^K35", "K35R"), (r"^H60|^S70", "H60"), (r"^EC|^AS|^H1[2-6]", "EC35"),
    (r"^B0|^B4[0-9]|^R[246]", "B407"), (r"^F1[5-8]|^F22|^F35", "F16"),
]
# Last resort: the ADS-B emitter category.
BY_CATEGORY = {"A1": "C172", "A2": "C25B", "A3": "B738", "A4": "B752", "A5": "B772", "A6": "F16", "A7": "EC35", "B1": "GLID"}

# Converted open-source models live in models/ at the top of the repo (see models/README.md).
# A type without its own file can borrow a close relative's. True marks relatives that look
# the same at this level of detail, so the panel doesn't call them a stand-in.
MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models")
RELATIVES = {
    "A19N": ("A319", True), "A20N": ("A320", True), "A21N": ("A321", True), "A338": ("A332", True), "A339": ("A333", True),
    "A35K": ("A359", False), "A342": ("A343", False), "B736": ("B737", False), "B37M": ("B737", True), "B38M": ("B738", True),
    "B39M": ("B739", True), "B3XM": ("B739", False), "B77L": ("B772", True), "B77W": ("B773", True),
    "B78X": ("B789", False),
    "E75L": ("E170", True), "E75S": ("E170", True), "E195": ("E190", False), "E290": ("E190", False), "E295": ("E190", False),
    "CRJX": ("CRJ9", False), "CRJ2": ("CRJ7", False),
    "C550": ("C560", False), "C551": ("C560", False), "C56X": ("C560", False), "C525": ("C560", False),
    "C25A": ("C560", False), "C25B": ("C560", False), "C510": ("C560", False),
    "AT43": ("AT45", True), "AT72": ("AT45", False), "AT75": ("AT45", False), "AT76": ("AT45", False),
    "DH8A": ("DH8D", False), "DH8B": ("DH8D", False), "DH8C": ("DH8D", False),
    "P28B": ("P28A", True), "P28R": ("P28A", True), "PA32": ("P28A", False), "P32R": ("P28A", False),
    "EC45": ("EC35", False), "EC30": ("EC35", False), "EC20": ("EC35", False), "AS50": ("EC35", False),
    "B429": ("EC35", False), "A109": ("EC35", False),
}

_built: dict = {}


def spec_for(type_code: str, category: str = ""):
    """(table key, matched exactly?) for an ICAO type code, falling back by family then category."""
    code = (type_code or "").upper().strip()
    if code in TYPES:
        return code, True
    for pattern, key in FAMILIES:
        if code and re.match(pattern, code):
            return key, False
    return BY_CATEGORY.get((category or "").upper().strip(), "B738"), False


def _converted(key: str):
    """A converted open-source model, or None if there is no file for this key."""
    if not re.fullmatch(r"[A-Z0-9]{2,4}", key):
        return None
    path = os.path.join(MODEL_DIR, key + ".json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            j = json.load(f)
        m = Model(str(j["name"]), str(j.get("kind", "jet")), float(j["length"]), float(j["span"]), int(j.get("engines", 2)))
        m.points = [(float(x), float(y), float(z)) for x, y, z in j["points"]]
        palette = [tuple(int(v) for v in c[:3]) for c in j["palette"]]
        n = len(m.points)
        solid = bool(j.get("solid", False))        # a closed skin can skip its back faces
        for a, b, c, colour in j["tris"]:
            if 0 <= a < n and 0 <= b < n and 0 <= c < n and 0 <= colour < len(palette):
                m.face((a, b, c), palette[colour], solid)
                if not any(m.faces[-1][3]):
                    m.faces.pop()                  # squashed to a line when the points were rounded: nothing to draw
        m.credit = "MODEL: " + str(j.get("credit", "OPEN SOURCE"))[:60]
        return m if m.faces else None
    except Exception:
        return None             # a damaged file just means the generated shape is used


def model_for(type_code: str, category: str = "") -> tuple[Model, bool]:
    """The mesh for a type, and whether it is that type or a stand-in.

    A converted open-source model wins if there is one for the type or a close relative.
    Otherwise the shape is generated from the type's dimensions.
    """
    code = (type_code or "").upper().strip()
    if not re.fullmatch(r"[A-Z0-9]{2,4}", code):
        code = ""                              # not a type code: use the category's stand-in
    for key, same in ((code, True), RELATIVES.get(code, (None, False))):
        if key:
            if ("file", key) not in _built:
                _built[("file", key)] = _converted(key)
            if _built[("file", key)]:
                return _built[("file", key)], same
    key, exact = spec_for(code, category)
    if key not in _built:
        kind, name, length, span, radius, opts = TYPES[key]
        _built[key] = helicopter(name, length, span, radius, **opts) if kind == "H" else airplane(name, length, span, radius, **opts)
    return _built[key], exact
