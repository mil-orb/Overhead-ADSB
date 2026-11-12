"""Settings sheet. Swipe up on the board, or press and hold, to open it.

Pages: the main list, the Wi-Fi picker, a password keyboard, and a location keypad.
Rows are sized for a resistive touchscreen. The sheet's edge follows the finger and
eases into place; see draw() for how that stays cheap enough for an SPI screen.
Changes are written into the config dict straight away and the names of changed keys
are collected in `changed`.
"""
from __future__ import annotations

import math
import threading
import time

import pygame

from .board import AMBER, INK, surf, text_line
from .tracker import DEFAULT_MODE, MODES

BG = (22, 23, 26)
HEAD = (33, 34, 38)
LINE = (46, 47, 52)
SUB = (150, 147, 136)
KEY = (52, 53, 58)
KEY_ALT = (38, 39, 43)
DARK_TXT = (20, 18, 14)
GOOD = (96, 222, 136)
BAD = (240, 96, 76)

KB = {
    "abc": ["qwertyuiop", "asdfghjkl", "zxcvbnm"],
    "ABC": ["QWERTYUIOP", "ASDFGHJKL", "ZXCVBNM"],
    "123": ["1234567890", "@#$%&*-+()", "!\"':;/?"],
    "#+=": ["[]{}<>^~`|", "\\_=.,+-*/", "!?@#$%&"],
}


class Settings:
    def __init__(self, screen, cfg, wifi, fonts, live=None, can_power=False, can_calibrate=False):
        self.scr, self.cfg, self.wifi, self.fonts = screen, cfg, wifi, fonts
        self.W, self.H = screen.get_size()
        self.live = live or (lambda key: None)
        self.can_power = can_power
        self.can_calibrate = can_calibrate
        self.s = s = max(0.75, min(self.W / 480, self.H / 320))
        self.hh = round(40 * s)
        self.rh = round(56 * s)
        self.pad = round(14 * s)
        self.cap = max(7, round(10 * s))
        self.cap2 = max(6, round(9 * s))
        self.sheet = surf(self.W, self.H)
        self.shown = self.sheet            # the sheet in the screen's pixel format, for fast blits
        self.veil = surf(self.W, self.H)
        self.veil.fill((0, 0, 0))
        self.backdrop = None
        self.off = float(self.H)
        self.target = float(self.H)
        self.visible = False
        self.opening = False
        self.closed_now = False
        motion = cfg.get("sheet_motion", "reveal")
        self.motion = motion if motion in ("reveal", "slide", "none") else "reveal"
        self._anim = None
        self._last_sheet = None
        self._flash_drawn = None
        self._dim_drawn = -1.0
        self.edge_h = max(4, round(8 * s))
        self.edge = surf(self.W, self.edge_h, True)          # soft shadow the sheet casts on the board above it
        for y in range(self.edge_h):
            pygame.draw.line(self.edge, (0, 0, 0, int(110 * ((y + 1) / self.edge_h) ** 2)), (0, y), (self.W, y))
        self.page = "main"
        self.scroll = {"main": 0, "wifi": 0, "show": 0}
        self.max_scroll = 0
        self.hits = []
        self.changed = set()
        self.action = None
        self.confirm = None
        self.dirty = True
        self._t = {}
        self.down_pos = None
        self.drag = None
        self.grab = 0
        self.scroll0 = 0
        self.flash = None
        self.slider = None
        self._drawn = -1
        # wifi
        self.ssid = None
        self.nets = None
        self.wifi_msg = ("", SUB)
        self.wifi_busy = None
        self.join_net = None
        self._results = []
        self._lock = threading.Lock()
        # keyboard and keypad
        self.kb_text, self.kb_mode, self.kb_show = "", "abc", True
        self.loc, self.loc_focus = ["", ""], 0

    # ------------------------------------------------------------ text ------
    def T(self, text, cap, color, keep=True):
        key = (text, cap, color)
        hit = self._t.get(key)
        if hit is None:
            hit = text_line(self.fonts, text, cap, color, max(1, round(cap * 0.12)), 0.95)
            if keep:
                if len(self._t) >= 300:
                    self._t.clear()
                self._t[key] = hit
        return hit

    def put(self, dst, text, cap, x, ymid, anchor="l", color=INK, max_w=None, keep=True):
        if max_w:
            full = text

            def width(v):
                t = self.T(v, cap, color)
                return t[0].get_width() if t else 0

            while len(text) > 1 and width(text if text == full else text.rstrip() + "..") > max_w:
                text = text[:-1]
            if text != full:
                text = text.rstrip() + ".."
        t = self.T(text, cap, color, keep)
        if not t:
            return 0
        a, top, capn = t
        w = a.get_width()
        x = x - w if anchor == "r" else x - w // 2 if anchor == "c" else x
        dst.blit(a, (x, ymid - top - capn // 2))
        return w

    # ----------------------------------------------------- open / close -----
    def _reset(self, backdrop):
        self.backdrop = backdrop
        self.visible, self.closed_now = True, False
        self.page, self.confirm, self.action = "main", None, None
        self.scroll = {"main": 0, "wifi": 0, "show": 0}
        self.changed = set()
        self.down_pos = self.drag = None
        self.dirty = True
        # The screen already shows the board, which is exactly the closed state, so nothing
        # needs sending until the edge moves.
        self._drawn, self._last_sheet, self._flash_drawn, self._anim = self.H, None, None, None
        self._dim_drawn = None
        self._work("current", lambda: self.wifi.current())

    def _go(self, target):
        """Send the sheet's top edge to `target` (0 open, H closed) with an ease-out."""
        self.target = float(target)
        dist = abs(self.target - self.off)
        if self.motion == "none" or dist < 1:
            self.off, self._anim = self.target, None
        else:
            self._anim = [self.off, self.target, 0.0, 0.14 + 0.2 * dist / self.H]

    def start_open(self, backdrop, pos):
        """Begin a finger-driven open: the sheet's edge follows the finger until it lets go."""
        self._reset(backdrop)
        if self.motion == "none":
            self.off = float(self.H)
            self._go(0)
            return
        self.opening = True
        self.grab = pos[1] - self.H
        self.off = float(self.H)
        self.target = float(self.H)

    def open(self, backdrop, page="main"):
        self._reset(backdrop)
        self.opening = False
        self.off = float(self.H)
        self._go(0)
        if page != "main":
            self.goto(page)

    def close(self):
        self._go(self.H)
        self.confirm = None

    def goto(self, page):
        self.page, self.confirm, self.dirty = page, None, True
        if page == "wifi" and self.wifi_busy is None:
            self.scan()
        if page == "loc":
            lat, lon = self.cfg.get("lat"), self.cfg.get("lon")
            self.loc = ["" if lat is None else f"{lat:.4f}", "" if lon is None else f"{lon:.4f}"]
            self.loc_focus = 0

    # ---------------------------------------------------------- workers -----
    def _work(self, tag, fn):
        def run():
            try:
                out = fn()
            except Exception as e:
                out = e
            with self._lock:
                self._results.append((tag, out))
        threading.Thread(target=run, daemon=True).start()

    def scan(self):
        self.wifi_busy = "scan"
        self.wifi_msg = ("LOOKING FOR NETWORKS", SUB)
        self.dirty = True
        self._work("scan", self.wifi.scan)

    def join(self, net, password):
        self.wifi_busy = "join"
        self.join_net = net
        self.wifi_msg = ("JOINING " + net.ssid, AMBER)
        self.page, self.dirty = "wifi", True
        self._work("join", lambda: self.wifi.connect(net.ssid, password))

    def _collect(self):
        with self._lock:
            results, self._results = self._results, []
        for tag, out in results:
            self.dirty = True
            if tag == "current":
                self.ssid = out if isinstance(out, str) else ""
            elif tag == "scan":
                self.wifi_busy = None
                if isinstance(out, Exception):
                    self.nets, self.wifi_msg = [], ("WI-FI IS NOT AVAILABLE", BAD)
                else:
                    self.nets = out
                    cur = next((n.ssid for n in out if n.in_use), "")
                    self.ssid = cur
                    if self.wifi_msg[1] is SUB:
                        self.wifi_msg = ("CONNECTED" if cur else "NOT CONNECTED", GOOD if cur else SUB)
            elif tag == "join":
                self.wifi_busy = None
                ok, msg = out if isinstance(out, tuple) else (False, "COULD NOT CONNECT")
                self.wifi_msg = (msg, GOOD if ok else BAD)
                if ok:
                    self.ssid = self.join_net.ssid
                    self.cfg["wifi_ssid"] = self.ssid
                    self.changed.add("wifi_ssid")
                    for n in self.nets or []:
                        n.in_use = n.ssid == self.ssid
                        n.saved = n.saved or n.in_use
                    (self.nets or []).sort(key=lambda n: (not n.in_use, -n.signal))

    # ------------------------------------------------------------ input -----
    def _set(self, key, value):
        if self.cfg.get(key) != value:
            self.cfg[key] = value
            self.changed.add(key)
            self.live(key)
        self.confirm = None
        self.dirty = True

    def _slide(self, x):
        x1, x2 = self.slider
        self._set("brightness", round(0.15 + 0.85 * max(0.0, min(1.0, (x - x1) / max(1, x2 - x1))), 2))

    def down(self, pos):
        self.down_pos, self.drag = pos, None
        if self.off > 1 or self.opening:
            return
        if self.page == "main" and self.slider and self.slider_rect.collidepoint(pos):
            self.drag = "slider"
            self._slide(pos[0])
            return
        for rect, fn, instant in reversed(self.hits):
            if rect.collidepoint(pos):
                self.flash = (rect, time.time() + (0.12 if instant else 10))
                self.dirty = True
                if instant:
                    fn()
                    self.down_pos = None
                return

    def move(self, pos):
        if self.opening:
            self.off = float(max(0, min(self.H, pos[1] - self.grab)))
            return
        if not self.down_pos:
            return
        dx, dy = pos[0] - self.down_pos[0], pos[1] - self.down_pos[1]
        if self.drag == "slider":
            self._slide(pos[0])
            return
        if self.drag is None and max(abs(dx), abs(dy)) > 10 * self.s:
            self.flash = None
            if self.down_pos[1] < self.hh and dy > 0:
                self.drag = "sheet"
            elif self.page in self.scroll:
                self.drag, self.scroll0 = "scroll", self.scroll[self.page]
            else:
                self.drag = "none"
        if self.drag == "sheet":
            self.off = float(max(0, dy))
        elif self.drag == "scroll":
            self.scroll[self.page] = max(0, min(self.max_scroll, self.scroll0 - dy))
            self.dirty = True

    def up(self, pos):
        if self.opening:
            self.opening = False
            self._go(0 if self.off < self.H * 0.95 else self.H)     # let go anywhere past the start: it opens
            return
        if self.drag == "sheet":
            self._go(self.H if self.off > self.H * 0.22 else 0)
        elif self.drag is None and self.down_pos:
            for rect, fn, instant in reversed(self.hits):
                if rect.collidepoint(pos) and rect.collidepoint(self.down_pos):
                    if not instant:
                        fn()
                    break
        self.flash = None if self.flash and self.flash[1] - time.time() > 1 else self.flash
        self.down_pos = self.drag = None
        self.dirty = True

    # ----------------------------------------------------------- frames -----
    def update(self, dt):
        """Advance animation and background work. True when the screen needs repainting."""
        self._collect()
        if self.wifi_busy:
            self.dirty = True                      # keeps the spinner turning
        if self.flash and time.time() > self.flash[1]:
            self.flash, self.dirty = None, True
        a = self._anim
        if a and not self.opening and self.drag != "sheet":
            a[2] += dt if a[2] > 0 else min(dt, 1 / 60)      # a long first frame must not eat the start
            t = min(1.0, a[2] / a[3])
            self.off = a[0] + (a[1] - a[0]) * (1 - (1 - t) ** 3)
            if t >= 1.0:
                self.off, self._anim = a[1], None
        if self.off >= self.H and self.target >= self.H and not self.opening and not self._anim:
            self.visible, self.closed_now = False, True
        return (self.dirty or int(self.off) != self._drawn or self._anim is not None
                or self._flash_rect() != self._flash_drawn)

    def _flash_rect(self):
        """Where the pressed-key highlight is on screen, or None."""
        if not self.flash:
            return None
        r, off = self.flash[0], int(self.off)
        if self.motion == "slide" or r.top < self.hh:
            r = r.move(0, off)
        return (r.x, r.y, r.w, r.h)

    def _diff(self, old):
        """Smallest rectangle covering every pixel of the sheet that differs from `old`."""
        full = pygame.Rect(0, 0, self.W, self.H)
        if old is None:
            return full
        d = self.sheet.copy()
        d.blit(old, (0, 0), special_flags=pygame.BLEND_RGB_SUB)
        e = old.copy()
        e.blit(self.sheet, (0, 0), special_flags=pygame.BLEND_RGB_SUB)
        d.blit(e, (0, 0), special_flags=pygame.BLEND_RGB_ADD)
        d.set_colorkey((0, 0, 0))
        r = d.get_bounding_rect()
        return r if r.w and r.h else None

    def draw(self, dim=0.0):
        """Paint the frame. Returns the rectangles that changed, for the display to send.

        "reveal" (the default) keeps the settings where they are. The header bar travels
        up from the bottom with a shadow ahead of it and uncovers them, so each frame
        only changes a band about as tall as the bar. That is what lets it run smoothly
        on an SPI panel. "slide" moves the whole sheet, which repaints most of the
        screen every frame.
        """
        scr, W, H, hh = self.scr, self.W, self.H, self.hh
        changed = None
        if self.dirty:
            self._render()
            changed = self._diff(self._last_sheet)
            self._last_sheet = self.sheet.copy()
            self.shown = self.sheet.convert(self.scr)
        off, prev = int(self.off), self._drawn
        reveal = self.motion != "slide"
        if reveal:
            if off > 0:
                scr.blit(self.backdrop, (0, 0), (0, 0, W, off))
                if off < H:
                    scr.blit(self.edge, (0, off - self.edge_h))
            if off < H:
                scr.blit(self.shown, (0, off), (0, 0, W, hh))
                if off + hh < H:
                    scr.blit(self.shown, (0, off + hh), (0, off + hh, W, H - off - hh))
        else:
            if off > 0:
                scr.blit(self.backdrop, (0, 0))
                self.veil.set_alpha(int(170 * (1 - off / H)))
                scr.blit(self.veil, (0, 0))
            scr.blit(self.shown, (0, off))
        flash = self._flash_rect()
        if flash:
            r = pygame.Rect(flash)
            hl = surf(r.w, r.h, True)
            pygame.draw.rect(hl, (255, 255, 255, 34), hl.get_rect(), border_radius=round(6 * self.s))
            scr.blit(hl, r)
        if dim > 0.004 and off < H:            # the board above the edge already carries its own dimming
            self.veil.set_alpha(int(255 * dim))
            scr.blit(self.veil, (0, off), (0, 0, W, H - off))

        screen = pygame.Rect(0, 0, W, H)
        dim_changed = self._dim_drawn is not None and abs(dim - self._dim_drawn) > 0.004
        if not reveal or dim_changed:
            rects = [screen]
        else:
            rects = []
            if off != prev:
                lo, hi = min(off, prev), max(off, prev)
                rects.append(pygame.Rect(0, lo - self.edge_h, W, hi - lo + hh + self.edge_h))
            if changed:
                if changed.top < hh:
                    rects.append(pygame.Rect(0, off, W, hh))
                body = changed.clip(pygame.Rect(0, off + hh, W, H))
                if body.w and body.h:
                    rects.append(body)
            for f in (flash, self._flash_drawn):
                if f and flash != self._flash_drawn:
                    rects.append(pygame.Rect(f))
            rects = [r.clip(screen) for r in rects]
        self._drawn, self._flash_drawn, self._dim_drawn = off, flash, dim
        return [r for r in rects if r.w and r.h]

    # --------------------------------------------------------- painting -----
    def _render(self):
        S = self.sheet
        S.fill(BG)
        self.hits = []
        self.slider = None
        self.max_scroll = 0
        getattr(self, "_page_" + self.page)(S)
        pygame.draw.line(S, (70, 71, 77), (0, 0), (self.W, 0))
        self.dirty = False

    def hit(self, rect, fn, instant=False):
        self.hits.append((pygame.Rect(rect), fn, instant))

    def _header(self, S, title, back=None, right=None):
        W, hh, s = self.W, self.hh, self.s
        pygame.draw.rect(S, HEAD, (0, 0, W, hh))
        pygame.draw.line(S, LINE, (0, hh - 1), (W, hh - 1))
        if back is None:
            pygame.draw.rect(S, (88, 89, 96), (W // 2 - round(18 * s), round(5 * s), round(36 * s), max(3, round(4 * s))),
                             border_radius=3)
            self.put(S, title, self.cap, W // 2, hh // 2 + round(3 * s), "c")
        else:
            self.put(S, title, self.cap, W // 2, hh // 2, "c")
            cx, cy, a = self.pad + round(5 * s), hh // 2, round(7 * s)
            pygame.draw.lines(S, INK, False, [(cx + a // 2, cy - a), (cx - a // 2, cy), (cx + a // 2, cy + a)],
                              max(2, round(2 * s)))
            self.hit((0, 0, round(64 * s), hh), back)
        if right:
            label, fn, color = right
            w = self.put(S, label, self.cap2, W - self.pad, hh // 2, "r", color)
            self.hit((W - w - 2 * self.pad, 0, w + 2 * self.pad, hh), fn)

    def _seg(self, S, r, options, current, on_pick):
        """Segmented control, right-aligned in row r."""
        s = self.s
        n = len(options)
        sw = min(round(62 * s), int((self.W * 0.60) / n))
        h = round(28 * s)
        x = self.W - self.pad - sw * n
        y = r.centery - h // 2
        pygame.draw.rect(S, KEY_ALT, (x, y, sw * n, h), border_radius=round(7 * s))
        for i, (label, value) in enumerate(options):
            cell = pygame.Rect(x + i * sw, y, sw, h)
            on = value == current
            if on:
                pygame.draw.rect(S, INK, cell.inflate(-round(4 * s), -round(4 * s)), border_radius=round(5 * s))
            self.put(S, label, self.cap2, cell.centerx, cell.centery, "c", DARK_TXT if on else SUB)
            self.hit((cell.x, r.y, sw, r.h), lambda v=value: on_pick(v))

    def _page_main(self, S):
        W, s, pad, rh = self.W, self.s, self.pad, self.rh
        cfg = self.cfg
        lat, lon = cfg.get("lat"), cfg.get("lon")
        unit = cfg.get("units", "mi")
        mode_name = next((name for key, name, _ in MODES if key == cfg.get("mode", DEFAULT_MODE)), "IN VIEW")
        per_mile = {"mi": 1.0, "km": 1.609344, "nm": 0.868976}.get(unit, 1.0)   # range is stored in miles
        rows = [
            ("WI-FI", "link", (self.ssid or ("..." if self.ssid is None else "NOT CONNECTED"), "wifi")),
            ("LOCATION", "link", ("NOT SET" if lat is None else f"{lat:.3f}, {lon:.3f}", "loc")),
            ("SHOW", "link", (mode_name, "show")),
            ("BRIGHTNESS", "slider", None),
            ("DIM AT NIGHT", "toggle", "night_dim"),
            ("DISTANCE", "seg", ("units", [("MI", "mi"), ("KM", "km"), ("NM", "nm")])),
            ("RANGE " + unit.upper(), "seg", ("range_mi", [(str(round(m * per_mile)), m) for m in (5, 10, 15, 25, 40)])),
            ("CLOCK", "seg", ("clock_24h", [("12 H", False), ("24 H", True)])),
            ("HORIZON", "seg", ("horizon_deg", [("0°", 0), ("5°", 5), ("10°", 10), ("15°", 15), ("20°", 20)])),
            ("EACH PLANE", "seg", ("dwell_s", [("6 S", 6), ("10 S", 10), ("15 S", 15), ("30 S", 30)])),
            ("FOLLOW UNTIL GONE", "toggle", "follow"),
            ("POWER", "power", None),
        ]
        if self.can_calibrate:
            rows.insert(len(rows) - 1, ("TOUCH", "button", ("CALIBRATE", "calibrate")))
        top = self.hh
        self.max_scroll = max(0, len(rows) * rh - (self.H - top))
        sc = self.scroll["main"] = max(0, min(self.max_scroll, self.scroll["main"]))
        S.set_clip((0, top, W, self.H - top))
        for i, (label, kind, arg) in enumerate(rows):
            r = pygame.Rect(0, top + i * rh - sc, W, rh)
            if r.bottom < top or r.top > self.H:
                continue
            self.put(S, label, self.cap, pad, r.centery)
            pygame.draw.line(S, LINE, (pad, r.bottom - 1), (W - pad, r.bottom - 1))
            if kind == "link":
                value, page = arg
                cx, a = W - pad - round(4 * s), round(6 * s)
                pygame.draw.lines(S, SUB, False, [(cx - a // 2, r.centery - a), (cx + a // 2, r.centery),
                                                  (cx - a // 2, r.centery + a)], max(2, round(2 * s)))
                self.put(S, value, self.cap2, cx - round(14 * s), r.centery, "r",
                         AMBER if value == "NOT SET" else SUB, max_w=int(W * 0.55))
                self.hit(r, lambda p=page: self.goto(p))
            elif kind == "slider":
                x1, x2 = int(W * 0.42), W - pad - round(12 * s)
                v = (float(cfg.get("brightness", 1.0)) - 0.15) / 0.85
                kx = int(x1 + (x2 - x1) * max(0.0, min(1.0, v)))
                th = max(3, round(5 * s))
                pygame.draw.rect(S, KEY, (x1, r.centery - th // 2, x2 - x1, th), border_radius=th)
                pygame.draw.rect(S, AMBER, (x1, r.centery - th // 2, kx - x1, th), border_radius=th)
                pygame.draw.circle(S, INK, (kx, r.centery), round(11 * s))
                self.slider = (x1, x2)
                self.slider_rect = pygame.Rect(x1 - round(20 * s), r.y, x2 - x1 + round(40 * s), r.h)
            elif kind == "toggle":
                on = bool(cfg.get(arg))
                tw, th = round(50 * s), round(28 * s)
                t = pygame.Rect(W - pad - tw, r.centery - th // 2, tw, th)
                pygame.draw.rect(S, AMBER if on else KEY, t, border_radius=th)
                pygame.draw.circle(S, INK, (t.right - th // 2 if on else t.left + th // 2, t.centery), th // 2 - round(3 * s))
                self.hit(r, lambda k=arg, v=not on: self._set(k, v))
            elif kind == "seg":
                key, options = arg
                self._seg(S, r, options, cfg.get(key), lambda v, k=key: self._set(k, v))
            elif kind == "button":
                label2, act = arg
                bw, bh = round(120 * s), round(30 * s)
                bt = pygame.Rect(W - pad - bw, r.centery - bh // 2, bw, bh)
                pygame.draw.rect(S, KEY, bt, border_radius=round(7 * s))
                self.put(S, label2, self.cap2, bt.centerx, bt.centery, "c", INK)
                self.hit(r, lambda a=act: self._act(a))
            elif kind == "power":
                buttons = [("RESTART", "restart"), ("SHUT DOWN", "shutdown")] if self.can_power else [("QUIT", "quit")]
                x = W - pad
                for label2, act in reversed(buttons):
                    armed = self.confirm == act
                    text = "TAP AGAIN" if armed else label2
                    bw, bh = round(104 * s), round(30 * s)
                    b = pygame.Rect(x - bw, r.centery - bh // 2, bw, bh)
                    pygame.draw.rect(S, BAD if armed else KEY, b, border_radius=round(7 * s))
                    self.put(S, text, self.cap2, b.centerx, b.centery, "c", INK)
                    self.hit(b.inflate(0, r.h - bh), lambda a=act: self._power(a))
                    x -= bw + round(10 * s)
        S.set_clip(None)
        if self.max_scroll:
            track = self.H - top
            bar = max(20, int(track * track / (len(rows) * rh)))
            y = top + int((track - bar) * sc / self.max_scroll)
            pygame.draw.rect(S, (70, 71, 77), (W - 4, y, 3, bar), border_radius=2)
        self._header(S, "SETTINGS", right=("DONE", self.close, AMBER))

    def _act(self, act):
        self.action = act
        self.close()

    def _power(self, act):
        if self.confirm == act:
            self.action = act
            self.close()
        else:
            self.confirm = act
        self.dirty = True

    def _page_show(self, S):
        """Pick which aircraft go on the card. One row per mode, with a line saying what it means."""
        W, s, pad = self.W, self.s, self.pad
        rh, top = round(60 * s), self.hh
        current = self.cfg.get("mode", DEFAULT_MODE)
        self.max_scroll = max(0, len(MODES) * rh - (self.H - top))
        sc = self.scroll["show"] = max(0, min(self.max_scroll, self.scroll["show"]))
        small = max(6, round(7 * s))
        S.set_clip((0, top, W, self.H - top))
        for i, (key, name, about) in enumerate(MODES):
            r = pygame.Rect(0, top + i * rh - sc, W, rh)
            if r.bottom < top or r.top > self.H:
                continue
            pygame.draw.line(S, LINE, (pad, r.bottom - 1), (W - pad, r.bottom - 1))
            on = key == current
            self.put(S, name, self.cap, pad, r.y + round(22 * s), "l", AMBER if on else INK)
            self.put(S, about.upper(), small, pad, r.y + round(42 * s), "l", SUB, max_w=W - 2 * pad - round(36 * s))
            if on:
                x, a = W - pad - round(18 * s), round(5 * s)
                pygame.draw.lines(S, AMBER, False, [(x, r.centery), (x + a, r.centery + a), (x + 2.4 * a, r.centery - a)],
                                  max(2, round(2.4 * s)))
            self.hit(r, lambda k=key: self._pick_mode(k))
        S.set_clip(None)
        if self.max_scroll:
            track = self.H - top
            bar = max(20, int(track * track / (len(MODES) * rh)))
            pygame.draw.rect(S, (70, 71, 77), (W - 4, top + int((track - bar) * sc / self.max_scroll), 3, bar), border_radius=2)
        self._header(S, "SHOW", back=lambda: self.goto("main"))

    def _pick_mode(self, key):
        self._set("mode", key)
        self.goto("main")

    def _page_wifi(self, S):
        W, s, pad, rh = self.W, self.s, self.pad, self.rh
        top = self.hh + round(30 * s)
        nets = self.nets or []
        self.max_scroll = max(0, len(nets) * rh - (self.H - top))
        sc = self.scroll["wifi"] = max(0, min(self.max_scroll, self.scroll["wifi"]))
        S.set_clip((0, top, W, self.H - top))
        for i, n in enumerate(nets):
            r = pygame.Rect(0, top + i * rh - sc, W, rh)
            if r.bottom < top or r.top > self.H:
                continue
            pygame.draw.line(S, LINE, (pad, r.bottom - 1), (W - pad, r.bottom - 1))
            x = pad
            if n.in_use:
                a = round(5 * s)
                pygame.draw.lines(S, GOOD, False, [(x, r.centery), (x + a, r.centery + a), (x + 2.4 * a, r.centery - a)],
                                  max(2, round(2.4 * s)))
            x += round(22 * s)
            bx = W - pad - round(26 * s)
            for b in range(4):                               # signal bars
                bh = round((4 + 4 * b) * s)
                col = INK if n.signal >= (15, 40, 60, 80)[b] else KEY
                pygame.draw.rect(S, col, (bx + b * round(7 * s), r.centery + round(8 * s) - bh, round(5 * s), bh),
                                 border_radius=1)
            if n.secure:                                     # padlock
                lx, lw, lh = bx - round(24 * s), round(12 * s), round(9 * s)
                pygame.draw.rect(S, SUB, (lx, r.centery - round(1 * s), lw, lh), border_radius=2)
                pygame.draw.arc(S, SUB, (lx + round(2 * s), r.centery - round(8 * s), lw - round(4 * s), round(14 * s)),
                                0, math.pi, max(2, round(2 * s)))
            self.put(S, n.ssid, self.cap, x, r.centery, "l", INK, max_w=bx - round(40 * s) - x)
            if n.saved and not n.in_use:
                pass
            self.hit(r, lambda net=n: self._choose(net))
        S.set_clip(None)
        if not nets and self.wifi_busy is None and self.nets is not None:
            self.put(S, "NO NETWORKS FOUND", self.cap, W // 2, top + rh, "c", SUB)
        # status line
        pygame.draw.rect(S, BG, (0, self.hh, W, top - self.hh))
        msg, col = self.wifi_msg
        mx = pad
        if self.wifi_busy:
            cx, cy, rr = pad + round(7 * s), self.hh + round(15 * s), round(6 * s)
            a0 = time.time() * 5
            pygame.draw.arc(S, AMBER, (cx - rr, cy - rr, 2 * rr, 2 * rr), a0, a0 + 4.2, max(2, round(2 * s)))
            mx += round(22 * s)
        self.put(S, msg.upper(), self.cap2, mx, self.hh + round(15 * s), "l", col, max_w=W - mx - pad)
        pygame.draw.line(S, LINE, (0, top - 1), (W, top - 1))
        self._header(S, "WI-FI", back=lambda: self.goto("main"),
                     right=None if self.wifi_busy else ("SCAN", self.scan, AMBER))

    def _choose(self, net):
        if self.wifi_busy or net.in_use:
            return
        if net.secure and not net.saved:
            self.join_net = net
            self.kb_text, self.kb_mode = "", "abc"
            self.page, self.dirty = "key", True
        else:
            self.join(net, None)

    def _page_key(self, S):
        W, H, s, pad = self.W, self.H, self.s, self.pad
        net = self.join_net
        fy, fh = self.hh + round(6 * s), round(34 * s)
        field = pygame.Rect(pad, fy, W - 2 * pad - round(64 * s), fh)
        pygame.draw.rect(S, (12, 12, 14), field, border_radius=round(6 * s))
        pygame.draw.rect(S, AMBER, field, max(1, round(1.5 * s)), border_radius=round(6 * s))
        shown = self.kb_text if self.kb_show else "*" * len(self.kb_text)
        if not self.kb_text:
            self.put(S, "PASSWORD", self.cap2, field.x + round(10 * s), field.centery, "l", (86, 86, 92))
            tw = 0
        else:
            # What is typed here is drawn fresh each time and never kept in the text cache.
            t, room = shown, field.w - round(24 * s)
            img = self.T(t, self.cap, INK, keep=False)
            while len(t) > 1 and img and img[0].get_width() > room:
                t = t[max(1, int(len(t) * (1 - room / img[0].get_width()))):]     # show the end of a long one
                img = self.T(t, self.cap, INK, keep=False)
            tw = self.put(S, t, self.cap, field.x + round(10 * s), field.centery, "l", INK, keep=False)
        cx = field.x + round(12 * s) + tw
        pygame.draw.line(S, AMBER, (cx, field.y + round(8 * s)), (cx, field.bottom - round(8 * s)), max(1, round(2 * s)))
        self.put(S, "HIDE" if self.kb_show else "SHOW", self.cap2, W - pad, field.centery, "r", SUB)
        self.hit((field.right, fy, W - field.right, fh), self._toggle_show)

        ky = field.bottom + round(6 * s)
        kh = (H - ky - round(4 * s)) // 4
        kw = W / 10.0
        rows = KB[self.kb_mode]

        def key(x, y, w, label, fn, alt=False, color=INK, enabled=True):
            r = pygame.Rect(round(x) + 2, y + 2, round(w) - 4, kh - 4)
            pygame.draw.rect(S, AMBER if (alt == "go" and enabled) else KEY_ALT if alt else KEY, r, border_radius=round(6 * s))
            if callable(label):
                label(r)
            else:
                self.put(S, label, round(self.cap * 1.25) if len(label) == 1 else self.cap2, r.centerx, r.centery,
                         "c", color if enabled else (96, 96, 100))
            if enabled:
                self.hit((round(x), y, round(w), kh), fn, True)

        for ri, chars in enumerate(rows[:2]):
            x0 = (W - len(chars) * kw) / 2
            for ci, c in enumerate(chars):
                key(x0 + ci * kw, ky + ri * kh, kw, c, lambda ch=c: self._type(ch))
        y3 = ky + 2 * kh
        letters = self.kb_mode in ("abc", "ABC")

        def shift_icon(r):
            if letters:
                a = round(8 * s)
                pts = [(r.centerx, r.centery - a), (r.centerx + a, r.centery), (r.centerx + a // 2, r.centery),
                       (r.centerx + a // 2, r.centery + a), (r.centerx - a // 2, r.centery + a),
                       (r.centerx - a // 2, r.centery), (r.centerx - a, r.centery)]
                if self.kb_mode == "ABC":
                    pygame.draw.polygon(S, AMBER, pts)
                else:
                    pygame.draw.polygon(S, INK, pts, max(2, round(2 * s)))
            else:
                self.put(S, "#+=" if self.kb_mode == "123" else "123", self.cap2, r.centerx, r.centery, "c", INK)

        def back_icon(r):
            a = round(8 * s)
            pts = [(r.centerx - a * 1.4, r.centery), (r.centerx - a * 0.5, r.centery - a), (r.centerx + a * 1.2, r.centery - a),
                   (r.centerx + a * 1.2, r.centery + a), (r.centerx - a * 0.5, r.centery + a)]
            pygame.draw.polygon(S, INK, pts, max(2, round(2 * s)))
            b = a * 0.42
            cx2 = r.centerx + a * 0.3
            pygame.draw.line(S, INK, (cx2 - b, r.centery - b), (cx2 + b, r.centery + b), max(2, round(2 * s)))
            pygame.draw.line(S, INK, (cx2 - b, r.centery + b), (cx2 + b, r.centery - b), max(2, round(2 * s)))

        key(0, y3, kw * 1.5, shift_icon, self._shift, True)
        for ci, c in enumerate(rows[2]):
            key(kw * 1.5 + ci * kw, y3, kw, c, lambda ch=c: self._type(ch))
        key(kw * 8.5, y3, kw * 1.5, back_icon, self._backspace, True)
        y4 = ky + 3 * kh
        key(0, y4, kw * 2, "123" if letters else "ABC", self._mode, True)
        key(kw * 2, y4, kw * 5, "SPACE", lambda: self._type(" "), False, SUB)
        ok = len(self.kb_text) >= 8
        key(kw * 7, y4, kw * 3, "JOIN", self._submit, "go", DARK_TXT, ok)
        self._header(S, (net.ssid if net else "PASSWORD")[:28], back=lambda: self.goto("wifi"))

    def _type(self, ch):
        if len(self.kb_text) < 63:
            self.kb_text += ch
        if self.kb_mode == "ABC":
            self.kb_mode = "abc"          # shift is one-shot, as on a phone
        self.dirty = True

    def _backspace(self):
        self.kb_text = self.kb_text[:-1]
        self.dirty = True

    def _shift(self):
        self.kb_mode = {"abc": "ABC", "ABC": "abc", "123": "#+=", "#+=": "123"}[self.kb_mode]
        self.dirty = True

    def _mode(self):
        self.kb_mode = "123" if self.kb_mode in ("abc", "ABC") else "abc"
        self.dirty = True

    def _toggle_show(self):
        self.kb_show = not self.kb_show
        self.dirty = True

    def _submit(self):
        if len(self.kb_text) >= 8 and self.join_net:
            pw, self.kb_text = self.kb_text, ""
            self.join(self.join_net, pw)

    def _loc_values(self):
        try:
            lat, lon = float(self.loc[0]), float(self.loc[1])
        except ValueError:
            return None
        return (lat, lon) if -90 <= lat <= 90 and -180 <= lon <= 180 else None

    def _page_loc(self, S):
        W, H, s, pad = self.W, self.H, self.s, self.pad
        fy, fh = self.hh + round(20 * s), round(34 * s)
        fw = (W - 3 * pad) // 2
        for i, label in enumerate(("LATITUDE", "LONGITUDE")):
            r = pygame.Rect(pad + i * (fw + pad), fy, fw, fh)
            self.put(S, label, max(6, round(7 * s)), r.x + 2, fy - round(9 * s), "l", SUB)
            pygame.draw.rect(S, (12, 12, 14), r, border_radius=round(6 * s))
            focus = i == self.loc_focus
            pygame.draw.rect(S, AMBER if focus else LINE, r, max(1, round(1.5 * s)), border_radius=round(6 * s))
            tw = self.put(S, self.loc[i], self.cap, r.x + round(10 * s), r.centery, "l", INK)
            if focus:
                cx = r.x + round(12 * s) + tw
                pygame.draw.line(S, AMBER, (cx, r.y + round(8 * s)), (cx, r.bottom - round(8 * s)), max(1, round(2 * s)))
            self.hit(r.inflate(0, round(16 * s)), lambda k=i: self._focus(k), True)
        hint = "DECIMAL DEGREES. SOUTH AND WEST ARE NEGATIVE."
        self.put(S, hint, max(6, round(7 * s)), pad + 2, fy + fh + round(10 * s), "l", SUB, max_w=W - 2 * pad)
        ky = fy + fh + round(20 * s)
        kh = (H - ky - round(4 * s)) // 4
        kw = W / 4.0
        ok = self._loc_values() is not None

        def key(col, row, span, label, fn, style=0, enabled=True):
            r = pygame.Rect(round(col * kw) + 2, ky + row * kh + 2, round(span * kw) - 4, kh - 4)
            pygame.draw.rect(S, AMBER if style == 2 and enabled else KEY_ALT if style else KEY, r, border_radius=round(6 * s))
            self.put(S, label, round(self.cap * 1.25) if len(label) == 1 else self.cap2, r.centerx, r.centery, "c",
                     (96, 96, 100) if not enabled else DARK_TXT if style == 2 else INK)
            if enabled:
                self.hit((round(col * kw), ky + row * kh, round(span * kw), kh), fn, True)

        grid = [("7", "8", "9", "DEL"), ("4", "5", "6", "-"), ("1", "2", "3", ".")]
        for ri, row in enumerate(grid):
            for ci, c in enumerate(row):
                key(ci, ri, 1, c, lambda ch=c: self._loc_key(ch), 1 if ci == 3 else 0)
        key(0, 3, 2, "0", lambda: self._loc_key("0"))
        key(2, 3, 2, "SAVE", self._loc_save, 2, ok)
        self._header(S, "LOCATION", back=lambda: self.goto("main"))

    def _focus(self, k):
        self.loc_focus = k
        self.dirty = True

    def _loc_key(self, ch):
        t = self.loc[self.loc_focus]
        if ch == "DEL":
            t = t[:-1]
        elif ch == "-":
            t = t[1:] if t.startswith("-") else "-" + t
        elif ch == ".":
            if "." not in t:
                t += "."
        elif len(t) < 10:
            t += ch
        self.loc[self.loc_focus] = t
        self.dirty = True

    def _loc_save(self):
        v = self._loc_values()
        if v:
            self._set("lat", round(v[0], 4))
            self._set("lon", round(v[1], 4))
            self._set("elevation_m", None)
            self.goto("main")
