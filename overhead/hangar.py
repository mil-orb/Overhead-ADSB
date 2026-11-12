"""The plane view: the aircraft on the card, drawn the way it looks from where you stand.

Tap the middle of the card and this panel wipes down over it. Tap again to close it,
or drag to look around the aircraft. The model is not a turntable.
It is posed from the live data: seen along your line of sight to the aircraft, pointing
along its track, nose up or down with its climb, banked if the feed reports a roll. An
airliner passing overhead shows its belly; one low in the east heading north shows its
left side.

Behind it is the sky in that direction as it is right now (see scene.py): the time of day,
the sun or moon if they are that way, the clouds from the weather feed, and the ground
below the horizon. The aircraft is lit by the same sun, so it goes orange at sunset and
shows its red, green and white lights at night.

The look is early-2000s console 3D: about a thousand flat polygons, each shaded from the
average of the faces around it so a curved skin reads as smooth. The model is drawn at
twice the window's size and averaged down, which takes the jaggies off. A Pi Zero does
that in about an eighth of a second, and the picture only changes when the aircraft's
attitude does, so it is redrawn a couple of times a second at most. While a finger is
dragging it the smoothing is skipped.
"""
from __future__ import annotations

import math

import pygame

from . import models
from .board import AMBER, INK, surf
from .scene import Scene, tone

LEVELS = 24                                         # steps of direct light per colour
FILL = 6                                            # steps of fill light
DAYLIGHT = ((0.62, 0.64, 0.68), (0.42, 0.41, 0.39), "day")      # ambient, direct, cache key

# Paint for the generated shapes, by part. Airlines get their tail colour from the callsign.
BASE = {"body": (232, 234, 236), "belly": (176, 182, 190), "glass": (28, 40, 60), "wing": (196, 200, 206),
        "tail": (70, 96, 150), "engine": (150, 156, 164), "dark": (40, 42, 48), "prop": (30, 30, 34)}
MILITARY = {"body": (112, 118, 124), "belly": (96, 102, 108), "wing": (104, 110, 116), "tail": (92, 98, 104),
            "engine": (84, 88, 94)}
TAILS = {"UAL": (20, 60, 150), "DAL": (170, 30, 50), "AAL": (150, 160, 172), "SWA": (60, 80, 200), "FDX": (90, 30, 150),
         "UPS": (80, 50, 24), "JBU": (20, 70, 150), "ASA": (16, 50, 96), "NKS": (250, 220, 20), "FFT": (20, 120, 80),
         "AAY": (30, 80, 170), "SCX": (240, 130, 30), "ACA": (200, 30, 40), "WJA": (20, 130, 140), "BAW": (20, 40, 100),
         "DLH": (20, 30, 80), "AFR": (30, 50, 130), "KLM": (0, 160, 220), "UAE": (190, 30, 40), "QTR": (100, 20, 60),
         "ENY": (150, 160, 172), "SKW": (20, 60, 150), "RPA": (20, 60, 150), "EDV": (170, 30, 50), "JIA": (150, 160, 172),
         "GTI": (20, 40, 100), "ABX": (200, 30, 40), "CKS": (200, 40, 40), "AMX": (20, 40, 90), "VOI": (150, 30, 140)}


def _snap(c):
    return tuple(max(0, min(255, int(v))) for v in c)


def livery(flight: str, military: bool, kind: str) -> dict:
    paint = dict(BASE)
    if military:
        paint.update(MILITARY)
    elif flight[:3] in TAILS:
        paint["tail"] = TAILS[flight[:3]]
    elif kind in ("prop", "heli", "glider"):
        paint["tail"] = (176, 44, 40)
    return paint


def pose(track, climb, roll, az, elev, yaw=0.0, tilt=0.0):
    """Matrix taking model coordinates (nose, right wing, up) to view coordinates
    (right on screen, up on screen, away from the eye), plus the eye-to-aircraft direction.

    track, az: degrees clockwise from north. climb: nose-up angle. roll: right wing down.
    yaw and tilt nudge the eye around the aircraft, for dragging.
    """
    t, c, r = math.radians(track), math.radians(climb), math.radians(roll)
    nose = (math.sin(t) * math.cos(c), math.cos(t) * math.cos(c), math.sin(c))                    # east, north, up
    right0 = (math.cos(t), -math.sin(t), 0.0)
    up0 = (-math.sin(t) * math.sin(c), -math.cos(t) * math.sin(c), math.cos(c))
    cr, sr = math.cos(r), math.sin(r)
    right = tuple(right0[i] * cr - up0[i] * sr for i in range(3))
    up = tuple(up0[i] * cr + right0[i] * sr for i in range(3))

    a, e = math.radians(az + yaw), math.radians(max(-89.0, min(89.0, elev + tilt)))
    f = (math.sin(a) * math.cos(e), math.cos(a) * math.cos(e), math.sin(e))                       # line of sight
    rt = (math.cos(a), -math.sin(a), 0.0)
    u = (rt[1] * f[2] - rt[2] * f[1], rt[2] * f[0] - rt[0] * f[2], rt[0] * f[1] - rt[1] * f[0])

    def row(v):
        return tuple(sum(v[i] * axis[i] for i in range(3)) for axis in (nose, right, up))
    return (row(rt), row(u), row(f))


def render(dst, model, m, paint, light, cx, cy, scale, shades, lighting=DAYLIGHT):
    """Draw the model onto dst. m is the pose matrix; light is a view-space direction to the light.

    Painter's algorithm: faces sorted far to near and drawn in that order, each one a flat
    colour. Closed models skip faces that look away. A face's colour is its paint times
    (ambient + direct), where direct follows its angle to the light and the ambient part is
    a little stronger on faces turned toward the eye, which keeps shape on the shaded side.
    """
    (a, b, c), (d, e, f), (g, h, i) = m
    amb, direct, tkey = lighting
    pts = model.points
    sx = [int(cx + (a * x + b * y + c * z) * scale + 0.5) for x, y, z in pts]
    sy = [int(cy - (d * x + e * y + f * z) * scale + 0.5) for x, y, z in pts]
    dz = [g * x + h * y + i * z for x, y, z in pts]
    # light direction in model space, so each face needs a single dot product
    lx = a * light[0] + d * light[1] + g * light[2]
    ly = b * light[0] + e * light[1] + h * light[2]
    lz = c * light[0] + f * light[1] + i * light[2]
    faces, soft = model.faces, model.lit
    order = []
    add = order.append
    k = 0
    for idx, part, solid, n in faces:
        away = n[0] * g + n[1] * h + n[2] * i             # > 0: the face looks away from the eye
        if away <= 0.02 or not solid:
            if len(idx) == 3:
                add(((dz[idx[0]] + dz[idx[1]] + dz[idx[2]]) / 3.0, k, away))
            else:
                add((sum([dz[j] for j in idx]) / len(idx), k, away))
        k += 1
    order.sort(reverse=True)                              # farthest first
    poly, line = pygame.draw.polygon, pygame.draw.line
    body = BASE["body"]
    top = LEVELS - 1
    for _, k, away in order:
        idx, part, solid, _ = faces[k]
        n = soft[k]
        base = paint.get(part, body) if part.__class__ is str else part
        lit = n[0] * lx + n[1] * ly + n[2] * lz
        face = -(n[0] * g + n[1] * h + n[2] * i)
        if away > 0:
            lit, face = -lit, -face                       # a thin surface seen from behind
        key = (base, int(lit * top + 0.5) if lit > 0 else 0, int(face * FILL + 0.5) if face > 0 else 0, tkey)
        col = shades.get(key)
        if col is None:
            q, p = key[1] / top, 0.70 + 0.30 * key[2] / FILL
            col = shades[key] = _snap([base[j] * (amb[j] * p + direct[j] * q) for j in range(3)])
        if len(idx) == 3:
            poly(dst, col, ((sx[idx[0]], sy[idx[0]]), (sx[idx[1]], sy[idx[1]]), (sx[idx[2]], sy[idx[2]])))
        elif len(idx) == 2:
            line(dst, col, (sx[idx[0]], sy[idx[0]]), (sx[idx[1]], sy[idx[1]]))
        else:
            poly(dst, col, [(sx[j], sy[j]) for j in idx])
    return len(order)


def lamps(dst, model, m, cx, cy, scale, size, strength):
    """Navigation lights: red on the left wingtip, green on the right, white at the tail."""
    (a, b, c), (d, e, f), _ = m
    for i, col in zip(model.lamps, ((255, 48, 40), (40, 255, 96), (255, 255, 255))):
        x, y, z = model.points[i]
        at = (int(cx + (a * x + b * y + c * z) * scale), int(cy - (d * x + e * y + f * z) * scale))
        pygame.draw.circle(dst, _snap([v * 0.45 * strength for v in col]), at, size * 2)
        pygame.draw.circle(dst, _snap([v * strength for v in col]), at, size)


class PlaneView:
    """Full-screen panel with the aircraft as seen from the observer."""

    def __init__(self, screen, board, cfg):
        self.scr, self.board, self.cfg = screen, board, cfg
        self.W, self.H = screen.get_size()
        W, H = self.W, self.H
        self.s = s = max(0.75, min(W / 480, H / 320))
        self.visible = False
        self.closed_now = False
        self.off = 0.0                      # how many lines of the panel are showing
        self._anim = None
        self._drawn = 0
        self.backdrop = None
        self.info = None
        self.yaw = self.tilt = 0.0          # drag offsets, degrees
        self._drag = None
        self._ease = None
        self._key = self._key_drawn = None
        self._text = self._text_drawn = None
        self._scope_img = None
        self.dirty = True
        self.shades = {}
        self.pad = pad = max(6, round(8 * s))
        self.top = round(30 * s)
        self.scope = pygame.Rect(pad, self.top, W - 2 * pad, H - self.top - round(74 * s))
        self.lw, self.lh = self.scope.size
        self.big = surf(self.lw * 2, self.lh * 2)       # drawn at twice the size, then smoothed down
        self.quick = surf(self.lw, self.lh)             # drawn 1:1 while a finger is moving it
        self.win = surf(self.lw, self.lh)               # the sky behind it, before doubling
        self.scene = Scene(self.lw, self.lh)
        self._glass = None
        self.panel = None                   # the whole panel, in the screen's format
        self._base = None                   # panel art without the scope and text

    # ---------------------------------------------------------------- data --
    def set(self, info):
        """info: dict with the live numbers (see App.plane_info)."""
        self.info = info
        self.scene.request(info["sky"])
        if self.scene.take_fresh():
            self._key_drawn = None          # a new sky has been painted
        key = (round(info["track"] / 2), round(info["climb"] / 2), round(info["roll"] / 3), round(info["az"] / 1.5),
               round(info["elev"] / 1.5), info["type"], info["flight"], round(self.yaw), round(self.tilt))
        text = (info["name"], info["operator"], info["dist"], info["alt"], info["dir"], info["hdg"], info["credit"])
        if key != self._key or text != self._text or self._key_drawn is None:
            self._key, self._text, self.dirty = key, text, True

    # ------------------------------------------------------- open / close ---
    def open(self, backdrop, info):
        self.backdrop = backdrop
        self.visible, self.closed_now = True, False
        self.yaw = self.tilt = 0.0
        self._drag = self._ease = None
        self.off, self._drawn = 0.0, 0
        self.set(info)
        if self.dirty or self.panel is None:
            self._compose()
        self._go(self.H)

    def close(self):
        self._go(0)

    def _go(self, target):
        dist = abs(target - self.off)
        self._anim = [self.off, float(target), 0.0, 0.14 + 0.2 * dist / self.H] if dist >= 1 else None
        if not self._anim:
            self.off = float(target)

    # --------------------------------------------------------------- input --
    def down(self, pos):
        self._drag = [pos, self.yaw, self.tilt, False]          # start, offsets at the start, has it moved
        self._ease = None

    def move(self, pos):
        d = self._drag
        if d and self.off >= self.H:
            (px, py), yaw0, tilt0, _ = d
            if d[3] or abs(pos[0] - px) + abs(pos[1] - py) > 14 * self.s:
                d[3] = True                                     # a drag: look around the aircraft
                self.yaw = max(-180.0, min(180.0, yaw0 - (pos[0] - px) * 0.6))
                self.tilt = max(-60.0, min(60.0, tilt0 + (pos[1] - py) * 0.4))
                if self.info:
                    self.set(self.info)

    def up(self, pos):
        """Finger lifted. A tap closes the panel; after a drag the view swings back to the real one."""
        d, self._drag = self._drag, None
        if not d:
            return
        if d[3]:
            if self.yaw or self.tilt:
                self._ease = [self.yaw, self.tilt, 0.0]
        elif self.off >= self.H:
            self.close()

    # -------------------------------------------------------------- frames --
    def update(self, dt):
        a = self._anim
        if a:
            a[2] += dt if a[2] > 0 else min(dt, 1 / 60)
            t = min(1.0, a[2] / a[3])
            self.off = a[0] + (a[1] - a[0]) * (1 - (1 - t) ** 3)
            if t >= 1.0:
                self.off, self._anim = a[1], None
                if self.off <= 0:
                    self.visible, self.closed_now = False, True
        e = self._ease
        if e and self.info:
            e[2] += dt
            t = min(1.0, e[2] / 0.35)
            k = (1 - t) ** 3
            self.yaw, self.tilt = e[0] * k, e[1] * k
            if t >= 1.0:
                self.yaw = self.tilt = 0.0
                self._ease = None
            self.set(self.info)
        return self.dirty or int(self.off) != self._drawn

    def draw(self):
        """Paint what changed. Returns the rectangles to send to the display."""
        scr, W, H = self.scr, self.W, self.H
        rects = []
        if self.dirty and self.info:
            changed = self._compose()
            if self.off > 0 and changed:
                rects.append(changed.clip(pygame.Rect(0, 0, W, int(self.off))))
        off, prev = int(self.off), self._drawn
        edge = max(3, round(4 * self.s))
        if off != prev or rects:
            if off > 0:
                scr.blit(self.panel, (0, 0), (0, 0, W, off))
            if off < H:
                scr.blit(self.backdrop, (0, off), (0, off, W, H - off))
                if off > 0:
                    pygame.draw.rect(scr, AMBER, (0, max(0, off - edge), W, min(edge, off)))     # the leading edge
        if off != prev:
            lo, hi = min(off, prev), max(off, prev)
            rects.append(pygame.Rect(0, lo - edge, W, hi - lo + edge).clip(scr.get_rect()))
        self._drawn = off
        return [r for r in rects if r.w and r.h]

    # ------------------------------------------------------------ painting --
    def _base_art(self):
        """Panel texture, the window's bezel, and the fixed lettering."""
        W, H, s, b = self.W, self.H, self.s, self.board
        art = surf(W, H)
        art.blit(b.texture, (0, 0))
        # A raised metal frame around a sunken window: shadow under the frame, a lit top edge,
        # a dark lip where the glass sits below it.
        t = max(3, round(5 * s))
        frame = self.scope.inflate(2 * t, 2 * t)
        rad = max(3, round(6 * s))
        pygame.draw.rect(art, (10, 10, 12), frame.move(0, max(2, round(3 * s))).inflate(2, 2), border_radius=rad + 1)
        pygame.draw.rect(art, (86, 88, 96), frame.move(0, -1), border_radius=rad)
        pygame.draw.rect(art, (22, 22, 25), frame.move(0, 1), border_radius=rad)
        pygame.draw.rect(art, (54, 55, 61), frame, border_radius=rad)
        pygame.draw.rect(art, (4, 4, 5), self.scope.inflate(2, 2), border_radius=2)
        pygame.draw.rect(art, (0, 0, 0), self.scope)
        sr = max(2, round(2.5 * s))
        for sx, sy in ((frame.left + t // 2 + 1, frame.top + t // 2), (frame.right - t // 2 - 2, frame.top + t // 2),
                       (frame.left + t // 2 + 1, frame.bottom - t // 2 - 1), (frame.right - t // 2 - 2, frame.bottom - t // 2 - 1)):
            pygame.draw.circle(art, (18, 18, 20), (sx, sy + 1), sr)
            pygame.draw.circle(art, (96, 98, 106), (sx, sy), sr - 1 if sr > 2 else sr)
        b.paint(art, "TAP TO CLOSE", max(5, round(7 * s)), W - self.pad, H - round(13 * s), "r", alpha=130)
        return art

    def _glass_art(self):
        """What makes the window look sunk into the panel: the frame's shadow falling across the
        top and sides of the picture, and a faint sheen on the glass."""
        if self._glass is None:
            w, h = self.scope.size
            g = surf(w, h, True)
            deep = max(8, round(16 * self.s))
            for i in range(deep):                           # shadow under the top edge, the heaviest
                pygame.draw.line(g, (0, 0, 0, int(150 * (1 - i / deep) ** 2)), (0, i), (w, i))
            side = max(5, round(9 * self.s))
            for i in range(side):                           # lighter down the sides and along the bottom
                a = int(90 * (1 - i / side) ** 2)
                pygame.draw.line(g, (0, 0, 0, a), (i, 0), (i, h))
                pygame.draw.line(g, (0, 0, 0, a), (w - 1 - i, 0), (w - 1 - i, h))
                pygame.draw.line(g, (0, 0, 0, a // 2), (0, h - 1 - i), (w, h - 1 - i))
            sheen = surf(w, h, True)                        # a soft diagonal reflection
            pygame.draw.polygon(sheen, (255, 255, 255, 8), [(w * 0.52, 0), (w * 0.70, 0), (w * 0.44, h), (w * 0.26, h)])
            pygame.draw.polygon(sheen, (255, 255, 255, 5), [(w * 0.74, 0), (w * 0.79, 0), (w * 0.53, h), (w * 0.48, h)])
            g.blit(sheen, (0, 0))
            self._glass = g
        return self._glass

    def prepare(self, info):
        """Draw the panel for this aircraft ahead of time, so opening it is instant."""
        self.set(info)
        if self.dirty and not self.visible:
            self._compose()

    def _compose(self):
        """Bring the panel up to date with the current data. Returns the screen rectangle that changed.

        The model is only redrawn when its attitude changed, and then only the scope's rectangle
        needs sending. New lettering repaints the whole panel.
        """
        info, b, s = self.info, self.board, self.s
        W, H = self.W, self.H
        if self._base is None:
            self._base = self._base_art().convert(self.scr)
            self._scope_img = surf(self.scope.w, self.scope.h).convert(self.scr)
        changed = None
        model, exact = models.model_for(info["type"], info["category"])
        if self._key != self._key_drawn:
            sky = info["sky"]
            moving = bool(self._ease or (self._drag and self._drag[3]))
            low, zoom = (self.quick, 1) if moving else (self.big, 2)
            look_az, look_up = info["az"] + self.yaw, max(-89.0, min(89.0, info["elev"] + self.tilt))
            if moving:
                self.scene.window(low, look_az, look_up)
            else:
                self.scene.window(self.win, look_az, look_up)
                pygame.transform.scale(self.win, low.get_size(), low)
            m = pose(info["track"], info["climb"], info["roll"], info["az"], info["elev"], self.yaw, self.tilt)
            # the sun, the moon, or a soft light from above, turned into view space
            amb, direct, (up, around), tkey = tone(sky)
            se, sa = math.radians(up), math.radians(around)
            sun = (math.sin(sa) * math.cos(se), math.cos(sa) * math.cos(se), math.sin(se))
            a, e = math.radians(look_az), math.radians(look_up)
            f = (math.sin(a) * math.cos(e), math.cos(a) * math.cos(e), math.sin(e))
            rt = (math.cos(a), -math.sin(a), 0.0)
            u = (rt[1] * f[2] - rt[2] * f[1], rt[2] * f[0] - rt[0] * f[2], rt[0] * f[1] - rt[1] * f[0])
            light = tuple(sum(sun[i] * axis[i] for i in range(3)) for axis in (rt, u, f))
            near = max(0.62, min(1.0, 1.0 - 0.38 * info["frac"]))         # closer aircraft are drawn a little bigger
            scale = 0.46 * min(self.lw, self.lh * 1.55) / model.radius * near * zoom
            render(low, model, m, livery(info["flight"], info["military"], model.kind), light,
                   self.lw * zoom // 2, self.lh * zoom // 2, scale, self.shades, (amb, direct, tkey))
            if sky.sun_elev < 2.0 and model.kind != "heli":
                lamps(low, model, m, self.lw * zoom // 2, self.lh * zoom // 2, scale, max(1, round(1.4 * self.s)) * zoom,
                      1.0 if sky.sun_elev < -4 else 0.75)
            self._scope_img.blit(low if moving else pygame.transform.smoothscale(low, self.scope.size), (0, 0))
            self._scope_img.blit(self._glass_art(), (0, 0))
            self._key_drawn = self._key
            changed = self.scope.copy()
        if self._text != self._text_drawn or self.panel is None:
            self._text_drawn = self._text
            changed = pygame.Rect(0, 0, W, H)

        art = self._base.copy()
        art.blit(self._scope_img, self.scope.topleft)
        cap, small = max(6, round(8 * s)), max(5, round(7 * s))
        x, y = self.scope.x + round(8 * s), self.scope.y + round(8 * s)

        def label(text, px, py, anchor="l"):
            # A dark edge around the letters, so they read over a white cloud as well as a night sky.
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (1, 1)):
                b.paint(art, text, cap, px + dx, py + dy, anchor, color=(0, 0, 0), alpha=150)
            b.paint(art, text, cap, px, py, anchor, color=INK)

        label("LOOK " + info["dir"], x, y)
        label(info["up"], x, y + round(14 * s))
        label(info["dist"], self.scope.right - round(8 * s), y, "r")
        label(info["alt"], self.scope.right - round(8 * s), y + round(14 * s), "r")
        label(info["hdg"], x, self.scope.bottom - round(16 * s))
        b.paint(art, info["flight"] or info["reg"], max(8, round(11 * s)), self.pad, round(9 * s), color=INK)
        b.paint(art, "AS SEEN FROM HERE", small, W - self.pad, round(11 * s), "r", alpha=170)
        y = self.scope.bottom + round(12 * s)
        b.paint(art, info["name"][:34], max(8, round(11 * s)), self.pad, y, color=INK)
        b.paint(art, info["operator"][:44], cap, self.pad, y + round(20 * s), alpha=200)
        note = info["credit"] if exact else "SIMILAR TYPE SHOWN.  " + info["credit"]
        b.paint(art, note[:56], small, self.pad, H - round(13 * s), alpha=130)
        self.panel = art
        self.dirty = False
        return changed
