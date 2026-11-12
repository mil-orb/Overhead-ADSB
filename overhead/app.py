"""Overhead: a split-flap card for the aircraft you could see from your window right now.

One aircraft fills the screen. When there are several it steps through them,
nearest first.

  python -m overhead                 live traffic (demo traffic until a location is set)
  python -m overhead --demo          simulated traffic, no network
  python -m overhead --size 480x320  window size for previewing on a PC
  python -m overhead --fullscreen    full screen on a desktop
  python -m overhead --fb            straight to the framebuffer; automatic on a Pi with no desktop
  python -m overhead --calibrate     redo the touch calibration (Pi only)
  python -m overhead --stats         print frame timing every 5 seconds

Touch: tap the right third for the next aircraft, the left third for the previous one,
the middle to see the aircraft as it looks from where you are (tap again to close, drag
to look around it). Press and hold to lock onto the aircraft showing until it leaves
the range. Swipe up from the lower part of the screen for settings.
Keys on a PC: S settings, V plane view, arrows previous/next, L or space lock,
F fast-forward, Esc quit.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import subprocess
import sys
import time

# Settings and cached data live in the folder that holds the package, next to this repo's README.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "config.json")

DEFAULTS = {
    "lat": None, "lon": None,       # set these in Settings > Location
    "elevation_m": None,            # None: looked up from the weather service
    "title": "OVERHEAD",
    "range_mi": 15,                 # only aircraft within this many miles are shown
    "units": "mi",                  # mi | km | nm
    "clock_24h": False,
    "horizon_deg": 5,               # lowest angle above the horizon you can see
    "horizon_mask": {},             # per direction, e.g. {"N": 25, "NE": 15}
    "mode": "view",                 # which aircraft go on the card: view | overhead | approaching | low | military | all
    "dwell_s": 10,                  # seconds each aircraft stays up
    "brightness": 1.0,
    "night_dim": True,
    "width": 480, "height": 320, "fullscreen": False,
    "follow": False,                # stay on each aircraft until it leaves the radius, no rotation
    "sheet_motion": "reveal",       # reveal (smooth on SPI) | slide (whole sheet moves) | none
    "stats": False,                 # print frame timing to the log every 5 seconds
    "poll_s": 5,
    "flaps_per_sec": 12,
    "fps": 30,
    "font": None,                   # path to a .ttf to use for the flap lettering
    "output": "auto",               # auto | window | fb (Linux framebuffer, for the Pi with no desktop)
    "fb_device": None,              # e.g. "/dev/fb1"; None picks the small panel
    "touch_device": None,           # e.g. "/dev/input/event0"; None finds the touch panel
    "touch_cal": None,              # written by the touch calibration screen
    "hints_seen": False,            # the touch tips screen has been shown and tapped away
}
UNIT_M = {"mi": 1609.344, "km": 1000.0, "nm": 1852.0}
MIN_DWELL = 4.0       # a newly arrived nearer aircraft waits this long before taking over
HOLD_S = 20.0         # after a tap, stay on that aircraft this long
LONG_PRESS = 0.7


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except FileNotFoundError:
        pass
    except Exception as e:
        print("config.json could not be read, using defaults:", e)
    if "show_hidden" in cfg:                 # setting from before the modes existed
        if cfg.pop("show_hidden") and cfg.get("mode") == "view":
            cfg["mode"] = "all"
    return cfg


def save_config(cfg):
    keep = {k: v for k, v in cfg.items() if k in DEFAULTS or k == "wifi_ssid"}
    tmp = CONFIG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(keep, f, indent=2)
    os.replace(tmp, CONFIG)


class Backlight:
    """Hardware backlight on Pi panels that expose one. Otherwise the app dims in software."""

    def __init__(self):
        self.path, self.max = None, 255
        for d in glob.glob("/sys/class/backlight/*"):
            try:
                with open(os.path.join(d, "max_brightness")) as f:
                    self.max = int(f.read().strip())
                if os.access(os.path.join(d, "brightness"), os.W_OK):
                    self.path = os.path.join(d, "brightness")
                    break
            except Exception:
                continue

    def set(self, level) -> bool:
        if not self.path:
            return False
        try:
            with open(self.path, "w") as f:
                f.write(str(max(1, int(self.max * level))))
            return True
        except Exception:
            self.path = None
            return False


class Stats:
    """Frame timing, printed every few seconds when "stats" is on. Shows up in the Pi's journal.

    draw is the time to advance and repaint the flaps; push is the copy to the framebuffer;
    lines is how many screen lines were sent, which is what the SPI link has to carry.
    """

    def __init__(self, every=5.0):
        self.every, self.t0 = every, time.perf_counter()
        self.reset()

    def reset(self):
        self.n = 0
        self.draw = self.push = self.draw_max = self.push_max = 0.0
        self.lines = 0

    def add(self, draw, push, rects, fb):
        if rects:
            self.n += 1
            self.draw += draw
            self.push += push
            self.draw_max, self.push_max = max(self.draw_max, draw), max(self.push_max, push)
            self.lines += max(r.bottom for r in rects) - min(r.top for r in rects)
        now = time.perf_counter()
        if now - self.t0 >= self.every:
            span = now - self.t0
            if self.n:
                print("stats: %.1f frames/s, draw %.1f ms avg %.1f max, push %.1f ms avg %.1f max, %d lines/s"
                      % (self.n / span, 1000 * self.draw / self.n, 1000 * self.draw_max,
                         1000 * self.push / self.n, 1000 * self.push_max, self.lines / span), flush=True)
            self.t0 = now
            self.reset()


class App:
    def __init__(self, args):
        import pygame
        from .board import Board
        from .hangar import PlaneView
        from .settings import Settings
        from .sky import FT, KT, assess, compass, sun_position
        from .tracker import MODES, Engine
        from .wifi import get_wifi
        self.pg, self.Board, self.Settings, self.Engine = pygame, Board, Settings, Engine
        self.PlaneView = PlaneView
        self.assess = assess
        self.mode_names = {key: name for key, name, _ in MODES}
        self.FT, self.KT, self.compass, self.sun = FT, KT, compass, sun_position

        self.args = args
        self.cfg = cfg = load_config()
        if args.lat is not None and args.lon is not None:
            cfg["lat"], cfg["lon"] = args.lat, args.lon
        if args.size:
            w, h = args.size.lower().split("x")
            cfg["width"], cfg["height"] = int(w), int(h)
        self.shot = bool(args.screenshot)
        self.fb = self.touch = None
        fbinfo = None
        mode = "fb" if args.fb else cfg.get("output", "auto")
        headless_linux = sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
        if not self.shot and (mode == "fb" or (mode == "auto" and headless_linux)):
            from . import device
            fbinfo = device.find_framebuffer(cfg.get("fb_device"))
            if not fbinfo and mode == "fb":
                sys.exit("No framebuffer found. Is the display overlay enabled? See docs/pi-setup.md.")
        if self.shot or fbinfo:
            os.environ["SDL_VIDEODRIVER"] = "dummy"
        elif sys.platform == "win32":
            # Without this, Windows display scaling (125%, 150%...) stretches the window, so a
            # 480x320 preview comes out larger than the Pi's screen and slightly blurred.
            try:
                import ctypes
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except Exception:
                pass
        pygame.display.init()
        pygame.font.init()
        if fbinfo:
            dev, w, h, bpp, stride = fbinfo
            pygame.display.set_mode((w, h))                # the dummy display, only there so events work
            self.fb = device.FbOut(dev, w, h, bpp, stride)
            self.scr = self.fb.surface()                   # drawn on directly, already in the panel's format
            print("drawing to %s, %dx%d at %d bpp" % (dev, w, h, bpp))
            tdev = device.find_touch(cfg.get("touch_device"))
            if tdev:
                self.touch = device.Touch((w, h), cfg.get("touch_cal"), self.touch_event)
                self.touch.start(tdev)
                print("touch on", tdev)
            else:
                print("no touch panel found")
        elif (args.fullscreen or cfg.get("fullscreen")) and not self.shot:
            self.scr = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
            pygame.mouse.set_visible(False)
        else:
            self.scr = pygame.display.set_mode((int(cfg["width"]), int(cfg["height"])))
        pygame.display.set_caption("Overhead")
        self.W, self.H = self.scr.get_size()

        self.backlight = Backlight()
        self.wifi = get_wifi(real=not self.shot)      # a screenshot never shows or touches real networks
        self.press = None
        self.hints_up = False           # the touch tips screen is showing
        self.fast = False
        self.running = True
        self.sim = 1_750_000_000.0 if self.shot else None
        self.build_board()
        self.settings = self.Settings(self.scr, cfg, self.wifi, self.board.fonts, self.on_live,
                                      can_power=sys.platform.startswith("linux"),
                                      can_calibrate=self.touch is not None)
        self.start_engine()

    # ------------------------------------------------------------ setup -----
    def now(self):
        return self.sim if self.sim is not None else time.time()

    def present(self, rects=None):
        """Put what was drawn in front of the user: the framebuffer on the Pi, the window elsewhere."""
        if self.fb:
            self.fb.push(self.scr, rects)
        elif rects:
            self.pg.display.update(rects)
        else:
            self.pg.display.flip()

    def touch_event(self, kind, pos):
        """Called on the touch thread. Hands touches to the main loop as ordinary mouse events."""
        pg = self.pg
        if kind == "down":
            pg.event.post(pg.event.Event(pg.MOUSEBUTTONDOWN, pos=pos, button=1))
        elif kind == "up":
            pg.event.post(pg.event.Event(pg.MOUSEBUTTONUP, pos=pos, button=1))
        else:
            pg.event.post(pg.event.Event(pg.MOUSEMOTION, pos=pos, rel=(0, 0), buttons=(1, 0, 0)))

    def calibrate(self):
        """Three taps on crosses to line the resistive panel up with the picture. Returns True if saved."""
        from . import device
        pg, t, scr = self.pg, self.touch, self.scr
        if not t:
            return False
        targets = device.calibration_targets(self.W, self.H)
        cap = max(8, round(self.H * 0.04))
        raws = []
        t.raw_mode = True
        while not t.raw_taps.empty():
            t.raw_taps.get()
        try:
            for i, (x, y) in enumerate(targets):
                scr.fill((0, 0, 0))
                self.board.paint(scr, "TAP THE CROSS  %d OF 3" % (i + 1), cap, self.W // 2, self.H // 2 - cap, "c",
                                 color=(242, 238, 226))
                self.board.paint(scr, "USE A FINGERNAIL OR STYLUS", max(6, cap * 2 // 3), self.W // 2,
                                 self.H // 2 + cap, "c", alpha=170)
                a = max(10, round(self.H * 0.05))
                pg.draw.line(scr, (238, 170, 34), (x - a, y), (x + a, y), 3)
                pg.draw.line(scr, (238, 170, 34), (x, y - a), (x, y + a), 3)
                self.present()
                try:
                    raws.append(t.raw_taps.get(timeout=120))
                except Exception:
                    return False                   # nobody tapped: keep the old calibration
                time.sleep(0.35)
            cal = device.solve_calibration(targets, raws)
            if not cal:
                return False
            t.cal = self.cfg["touch_cal"] = cal
            try:
                save_config(self.cfg)
            except Exception as e:
                print("could not save config:", e)
            return True
        finally:
            t.raw_mode = False
            pg.event.clear()
            self.present(self.board.redraw())

    def build_board(self):
        self.board = self.Board(self.scr, self.cfg)
        self.view = self.PlaneView(self.scr, self.board, self.cfg)
        self.view_hex, self._view_at = None, 0.0
        self._clock_at = self._dim_at = self._apply_at = 0.0
        self.frame = None

    def start_engine(self):
        if getattr(self, "engine", None):
            self.engine.stop()
        cfg = dict(self.cfg)
        self.unset = cfg.get("lat") is None or cfg.get("lon") is None
        self.demo = self.args.demo or self.unset
        if self.unset:
            cfg["lat"], cfg["lon"] = 41.5, -93.6      # only used to place the demo traffic
        self.engine = self.Engine(cfg, self.demo, os.path.join(ROOT, "data"))
        self.engine.demo_sky = getattr(self.args, "sky", "day")
        self.engine_cfg = cfg
        self.cur, self.since, self.hold_until = None, 0.0, 0.0
        self.known, self.jump, self.lock = set(), None, None
        self.frame = None
        if not self.shot:
            self.engine.start()

    # --------------------------------------------------------- rotation -----
    def rotate(self, frame, now):
        """Pick which aircraft is on the card. Nearest visible first, then step through the rest."""
        deck = frame.sightings[:self.board.MAX_PAGES]
        hexes = [s.hex for s in deck]
        if self.lock and not frame.find_any(self.lock):
            self.lock, self.cur = None, None         # the locked aircraft left the radius: let go
        if self.lock:
            self.cur = self.lock                     # locked: stay on it, visible or not
        elif not hexes:
            self.cur, self.jump = None, None
        elif self.cur not in hexes:
            self.cur, self.since, self.hold_until = hexes[0], now, 0.0
        else:
            for s in deck:                       # something new worth interrupting for
                if s.hex not in self.known and s.hex != self.cur:
                    if s.plane.military or s.plane.emergency or (s is deck[0] and s.state == "view"):
                        self.jump = s.hex
            if self.jump not in hexes:
                self.jump = None
            dwell = float(self.cfg.get("dwell_s", 10)) * (1.5 if self.cur == hexes[0] else 1.0)
            free = now >= self.hold_until
            if self.jump and free and now - self.since >= MIN_DWELL:
                self.cur, self.since, self.jump = self.jump, now, None
            elif len(hexes) > 1 and free and now - self.since >= dwell:
                self.cur, self.since = hexes[(hexes.index(self.cur) + 1) % len(hexes)], now
        if self.cfg.get("follow") and self.cur:
            self.lock = self.cur                     # follow mode: whatever is up stays up until it is gone
        self.known = set(hexes)
        return deck, (hexes.index(self.cur) if self.cur in hexes else -1)

    def step_card(self, delta):
        frame = self.frame
        if not frame or not frame.sightings:
            return
        hexes = [s.hex for s in frame.sightings[:self.board.MAX_PAGES]]
        i = hexes.index(self.cur) if self.cur in hexes else (-1 if delta > 0 else 0)
        self.cur = hexes[(i + delta) % len(hexes)]
        self.since = self.now()
        self.hold_until = self.since + HOLD_S
        self.jump, self.lock = None, None
        self._apply_at = 0.0

    def toggle_lock(self):
        """Lock onto the aircraft on the card until it leaves the radius, or let it go."""
        if self.cur:
            self.lock = None if self.lock == self.cur else self.cur
            self.since = self.now()
            self._apply_at = 0.0

    def live(self, s, frame, now):
        """The same sighting moved along its track to this instant, so the numbers keep ticking
        between data updates. The visibility verdict stays the tracker's, which has hysteresis."""
        fresh = self.assess(self.engine.obs, frame.sky, s.plane, now)
        fresh.state, fresh.reason, fresh.inbound_s, fresh.route = s.state, s.reason, s.inbound_s, s.route
        fresh.closing = s.closing
        return fresh

    # ------------------------------------------------------ card content ----
    def card(self, s):
        p, r = s.plane, s.route or {}
        d = s.rng / UNIT_M[self.cfg.get("units", "mi")]
        if p.emergency:
            remark = "EMERGENCY"
        elif p.military:
            remark = "MILITARY"
        elif s.state == "view":
            remark = "IN VIEW"
        elif s.state == "faint":
            remark = "FAINT"
        else:
            remark = "INBOUND" if s.inbound_s is not None else s.reason
        return {
            "flight": (p.flight or p.reg or p.hex.upper())[:7],
            "from": r.get("from", ""), "to": r.get("to", ""),
            "type": p.type[:4],
            "kt": "%d" % min(999, round(p.gs / self.KT)),
            "alt": "%03d" % max(0, min(999, int(round(s.alt_m / self.FT / 100)))),
            "dist": "%.1f" % d if d < 9.95 else str(int(round(d))),
            "dir": self.compass(s.az),
            "elev": str(int(round(max(0.0, s.elev)))),
            "remark": remark,
            "view": s.state == "view",
            "mil": 2 if p.emergency else 1 if p.military else 0,
        }

    def apply(self, frame, now):
        b = self.board
        # A frame that has stopped being replaced means the tracker is stuck. Say so, the same
        # way as a lost feed, instead of showing old positions under a green lamp.
        stale = bool(frame.stamp) and self.engine.stuck()
        online = frame.online and not stale
        if stale:
            frame = self.empty_frame(frame)
        deck, index = self.rotate(frame, now)
        s = frame.find_any(self.cur) if self.cur else None
        if s:
            b.set_card(self.card(self.live(s, frame, now)))
        elif frame.stamp == 0:
            b.set_card({})                               # still starting up: blank, lamps off
        elif not online:
            b.set_card(None, alarm=True, message="NO DATA")   # lost the feed: red lamp blinks, flaps say why
        else:
            b.set_card(None)                             # nothing to show: every flap blank, red lamp
        b.set_pages(len(deck), index, locked=bool(self.lock))
        if frame.stamp:
            sky = frame.sky.word()
            left = "%d IN RANGE   %d IN VIEW" % (frame.in_range, frame.in_view) + ("   " + sky if sky else "")
            mode = self.cfg.get("mode", "view")
            if mode != "view":                           # say why the card may be skipping aircraft
                left = "%s ONLY   %s" % (self.mode_names.get(mode, mode.upper()), left) if mode != "all" else "EVERYTHING   " + left
            if self.unset:
                left = "DEMO TRAFFIC.  SWIPE UP TO SET YOUR LOCATION"
            b.set_status(left, frame.source if online else "NO DATA", online)

    @staticmethod
    def empty_frame(frame):
        """The same frame with no aircraft in it."""
        import dataclasses
        return dataclasses.replace(frame, sightings=[], everyone=[], in_range=0, in_view=0, online=False)

    def tick_clock(self):
        t = time.localtime(self.now())
        if self.cfg.get("clock_24h"):
            text = "%02d%02d" % (t.tm_hour, t.tm_min)
        else:
            text = "%2d%02d" % (t.tm_hour % 12 or 12, t.tm_min)
        self.board.set_clock(text)

    def level(self):
        """Screen brightness 0..1 after the night setting."""
        level = max(0.15, min(1.0, float(self.cfg.get("brightness", 1.0))))
        if self.cfg.get("night_dim") and not self.demo:
            elev, _ = self.sun(self.engine_cfg["lat"], self.engine_cfg["lon"], self.now())
            if elev < -4:
                level *= 0.45
        return level

    def soft_dim(self):
        return 0.0 if self.backlight.path else (1 - self.level()) * 0.8

    def apply_brightness(self):
        level = self.level()
        if self.backlight.set(level):
            return self.board.set_dim(0.0)
        return self.board.set_dim((1 - level) * 0.8)

    def on_live(self, key):
        if key == "brightness" and self.backlight.path:
            self.backlight.set(self.level())

    # ------------------------------------------------------------ input -----
    def show_hints(self):
        """Put the touch tips over the card until the next tap."""
        from . import hints
        self.press = None
        self.hints_up = True
        self.present([hints.draw(self.scr, self.board, self.scr.copy())])

    def close_hints(self):
        self.hints_up = False
        if not self.cfg.get("hints_seen"):
            self.cfg["hints_seen"] = True
            try:
                save_config(self.cfg)
            except Exception as e:
                print("could not save config:", e)
        self._clock_at = self._apply_at = 0.0
        self.present(self.board.redraw())

    def open_settings(self, pos=None):
        """Open settings. With `pos`, the sheet's edge starts under that finger and follows it."""
        self.press = None
        if pos:
            self.settings.start_open(self.scr.copy(), pos)
        else:
            self.settings.open(self.scr.copy())

    def after_settings(self):
        st = self.settings
        st.closed_now = False
        ch = st.changed
        if ch:
            try:
                save_config(self.cfg)
            except Exception as e:
                print("could not save config:", e)
        if "units" in ch:
            self.build_board()
            st.fonts = self.board.fonts
        if ch & {"lat", "lon"}:
            self.start_engine()
        elif ch & {"horizon_deg", "mode", "elevation_m", "range_mi"}:
            self.engine_cfg = self.engine_cfg | {k: self.cfg[k] for k in ("horizon_deg", "mode", "range_mi")}
            self.engine.configure(self.engine_cfg)
            self.frame = None
        self._clock_at = self._dim_at = self._apply_at = 0.0
        self.present(self.board.redraw())
        if st.action == "calibrate":
            self.calibrate()
        elif st.action == "tips":
            self.show_hints()
        elif st.action == "quit":
            self.running = False
        elif st.action == "restart":
            self.pg.quit()
            os.chdir(ROOT)
            os.execv(sys.executable, [sys.executable, "-m", "overhead"] + sys.argv[1:])
        elif st.action == "shutdown":
            subprocess.run(["sudo", "-n", "systemctl", "poweroff"], check=False)
            self.running = False

    def event(self, e):
        """Touch and keys.

        On the card: tap the left or right third to step through aircraft, tap the middle
        to see the aircraft, press and hold to lock onto it, swipe up from the lower part
        for settings. In the plane view: tap to close, drag to look around.
        """
        pg, st, view = self.pg, self.settings, self.view
        if e.type == pg.QUIT:
            self.running = False
        elif self.hints_up:
            # The tap that puts the tips away does nothing else: closing on the lift, so the
            # finger that went down on the tips doesn't land on the card.
            if e.type in (pg.MOUSEBUTTONUP, pg.KEYDOWN):
                self.close_hints()
        elif e.type == pg.KEYDOWN:
            if e.key == pg.K_ESCAPE:
                if st.visible:
                    st.close()
                elif view.visible:
                    view.close()
                else:
                    self.running = False
            elif st.visible:
                return
            elif e.key in (pg.K_v, pg.K_RETURN):
                view.close() if view.visible else self.open_view()
            elif view.visible:
                return
            elif e.key == pg.K_s:
                self.open_settings()
            elif e.key == pg.K_f:
                self.fast = not self.fast
            elif e.key == pg.K_RIGHT:
                self.step_card(1)
            elif e.key == pg.K_LEFT:
                self.step_card(-1)
            elif e.key in (pg.K_l, pg.K_SPACE):
                self.toggle_lock()
        elif e.type == pg.MOUSEBUTTONDOWN and e.button == 1:
            if st.visible:
                st.down(e.pos)
            elif view.visible:
                view.down(e.pos)
            else:
                self.press = (e.pos, time.time())
        elif e.type == pg.MOUSEMOTION:
            if st.visible:
                st.move(e.pos)
            elif view.visible:
                view.move(e.pos)
            elif self.press:
                (px, py), _ = self.press
                if py >= self.H * 0.6 and py - e.pos[1] > max(24, self.H // 12):
                    self.open_settings((px, py))              # swipe up from the lower part of the screen
                    st.move(e.pos)
                elif abs(e.pos[0] - px) + abs(e.pos[1] - py) > 30:
                    self.press = None                         # a drag, not a tap or a hold
        elif e.type == pg.MOUSEBUTTONUP and e.button == 1:
            if st.visible:
                st.up(e.pos)
            elif view.visible:
                view.up(e.pos)
            elif self.press:
                self.press = None
                third = e.pos[0] * 3 // self.W
                if third == 1:
                    self.open_view()
                else:
                    self.step_card(-1 if third == 0 else 1)

    # ------------------------------------------------------- plane view -----
    def plane_info(self, s, frame):
        """Everything the plane view shows, from one live sighting."""
        from . import models
        p, r = s.plane, s.route or {}
        units = self.cfg.get("units", "mi")
        d = s.rng / UNIT_M[units]
        model, _ = models.model_for(p.type, p.category)
        feet = units != "km"
        height = s.alt_m / self.FT if feet else s.alt_m
        return {
            "type": p.type, "category": p.category, "flight": p.flight, "reg": p.reg, "military": p.military,
            "name": (p.desc or model.name).upper(),
            "operator": "   ".join(t for t in ((r.get("airline") or p.owner).upper(), p.reg) if t),
            "track": p.track, "roll": p.roll, "climb": math.degrees(math.atan2(p.vrate, max(p.gs, 30.0))),
            "az": s.az, "elev": s.elev, "frac": min(1.0, s.rng / self.engine.obs.range_m),
            "dist": ("%.1f %s" % (d, self.board.unit_label)) if d < 9.95 else "%d %s" % (round(d), self.board.unit_label),
            "alt": "%s %s" % (format(int(round(height / 100.0)) * 100, ","), "FT" if feet else "M"),
            "dir": self.compass(s.az), "up": "%d° UP" % round(max(0.0, s.elev)),
            "hdg": "HEADING %03d°" % (round(p.track) % 360),
            "sky": frame.sky,
            "credit": model.credit,
        }

    def open_view(self):
        """Show the aircraft on the card as it looks from here. Does nothing when the card is empty."""
        frame = self.frame
        s = frame.find_any(self.cur) if (frame and self.cur) else None
        if not s:
            return
        self.press = None
        self.view_hex, self._view_at = s.hex, self.now()
        self.view.open(self.scr.copy(), self.plane_info(self.live(s, frame, self.now()), frame))

    def tick_view(self, dt):
        """One frame of the plane view: fresh numbers twice a second, repaint what changed."""
        view, now = self.view, self.now()
        if now - self._view_at >= 0.5 and not view.closed_now:
            self._view_at = now
            frame = self.frame = self.engine.latest()
            s = frame.find_any(self.view_hex)
            if s:
                view.set(self.plane_info(self.live(s, frame, now), frame))
            elif view.off >= self.H:
                view.close()                                  # it left the range while we were looking
        rects = view.draw() if view.update(dt) else []
        if view.closed_now:
            view.closed_now = False
            self.hold_until = self.now() + HOLD_S             # stay on that aircraft for a moment
            self._clock_at = self._apply_at = 0.0
            rects = self.board.redraw()
        return rects

    # ------------------------------------------------------------- loop -----
    def step(self, dt):
        """One frame of the board. Returns the rectangles that changed."""
        now = self.now()
        frame = self.engine.latest()
        if frame is not self.frame or now - self._apply_at >= 0.5:
            self.frame, self._apply_at = frame, now
            self.apply(frame, now)
        rects = []
        if now - self._clock_at >= 1:
            self._clock_at = now
            self.tick_clock()
        if now - self._view_at >= 1.0 and not self.board.busy and self.cur:
            self._view_at = now                    # while nothing is moving, get the plane view ready
            s = frame.find_any(self.cur)
            if s:
                self.view.prepare(self.plane_info(self.live(s, frame, now), frame))
        if now - self._dim_at >= 30:
            self._dim_at = now
            rects += self.apply_brightness()
        return rects + self.board.update(dt * (4 if self.fast else 1))

    def run(self):
        pg = self.pg
        clock = pg.time.Clock()
        fps = int(self.cfg.get("fps", 30))
        stats = Stats() if (self.cfg.get("stats") or getattr(self.args, "stats", False)) else None
        budget = self.fb.lines_per_second if self.fb else None     # what the link to the panel can carry
        lines = 0
        self.present(self.board.redraw())
        if self.touch and (self.args.calibrate or not self.cfg.get("touch_cal")):
            self.calibrate()                       # first run on the Pi, or asked for
        if not self.cfg.get("hints_seen"):
            self.show_hints()                      # once, so the touch zones aren't a guessing game
        while self.running:
            moving = self.board.busy or self.settings.visible or self.view.visible or self.press
            rate = fps if moving else 20
            if budget and lines:
                # No point drawing frames faster than the panel can receive them: the extra ones are
                # never seen and they take the CPU the SPI driver needs.
                rate = max(5.0, min(rate, 1.25 * budget / lines))
            dt = min(0.2, clock.tick(rate) / 1000.0)
            for e in pg.event.get():
                self.event(e)
            if self.hints_up:
                if stats:
                    stats.add(0.0, 0.0, [], self.fb)
                continue                           # a still picture: nothing to draw until it is tapped away
            if self.press and time.time() - self.press[1] >= LONG_PRESS:
                self.press = None
                self.toggle_lock()                 # press and hold: lock onto this aircraft, or let it go
            t0 = time.perf_counter()
            if self.settings.visible:
                rects = self.settings.draw(self.soft_dim()) if self.settings.update(dt) else []
            elif self.view.visible:
                rects = self.tick_view(dt)
            else:
                rects = self.step(dt)
            t1 = time.perf_counter()
            if rects:
                self.present(rects)
                lines = max(r.bottom for r in rects) - min(r.top for r in rects)
            else:
                lines = 0
            if self.settings.closed_now:
                self.after_settings()
            if stats:
                stats.add(t1 - t0, time.perf_counter() - t1, rects, self.fb)
        self.engine.routes.save()
        if self.fb:
            self.fb.close()
        pg.quit()

    def capture(self):
        """Headless render for checking the look: python -m overhead --demo --screenshot out.png --at 0.4,8"""
        pg = self.pg
        times = sorted(float(t) for t in str(self.args.at).split(","))
        base, ext = os.path.splitext(self.args.screenshot)
        t, dt, next_poll, start = 0.0, 1 / 60, 0.0, self.sim
        for k, stop in enumerate(times):
            while t < stop:
                if t >= next_poll:
                    self.engine.step(self.sim)
                    next_poll += self.engine.poll
                self.step(dt)
                t += dt
                self.sim = start + t
            if getattr(self.args, "plane", False):
                self.open_view()
                if self.view.info:
                    self.view.scene.wait()              # the sky is painted on a thread; a still wants it finished
                    self.view.set(self.view.info)
                for _ in range(200):
                    rects = self.tick_view(1 / 30)
                    if self.view.off >= self.H and not rects:
                        break
            if getattr(self.args, "hints", False):
                from . import hints
                hints.draw(self.scr, self.board, self.scr.copy())
            if self.args.settings:
                st = self.settings
                st.open(self.scr.copy(), self.args.settings)
                if self.args.settings == "key":
                    st.join_net = type("N", (), {"ssid": "Workshop"})()
                    st.kb_text = "correct horse"
                deadline = time.time() + 3
                while time.time() < deadline:
                    st.update(0.05)
                    if st.wifi_busy is None and st.off == 0:
                        break
                    time.sleep(0.05)
                st.update(0.05)
                st.draw(0.0)
            name = self.args.screenshot if len(times) == 1 else f"{base}_{k}{ext}"
            pg.image.save(self.scr, name)
            print("saved", name, "t=%.2f" % t, "moving", len(self.board.active), "of", len(self.board.modules),
                  "| flap width", self.board.U)
        pg.quit()


def main():
    ap = argparse.ArgumentParser(description="Split-flap card for the aircraft visible overhead")
    ap.add_argument("--demo", action="store_true", help="simulated traffic, no network")
    ap.add_argument("--fullscreen", action="store_true")
    ap.add_argument("--size", help="window size, e.g. 480x320")
    ap.add_argument("--lat", type=float)
    ap.add_argument("--lon", type=float)
    ap.add_argument("--fb", action="store_true", help="draw to the Linux framebuffer (Pi with no desktop)")
    ap.add_argument("--calibrate", action="store_true", help="run touch calibration at start")
    ap.add_argument("--stats", action="store_true", help="print frame timing every 5 seconds")
    ap.add_argument("--screenshot", help="render headless and save a PNG")
    ap.add_argument("--at", default="8", help="seconds into the run to capture; comma separated for several")
    ap.add_argument("--settings", help="with --screenshot: capture this settings page (main, wifi, key, loc, show)")
    ap.add_argument("--plane", action="store_true", help="with --screenshot: capture the plane view")
    ap.add_argument("--hints", action="store_true", help="with --screenshot: capture the touch tips screen")
    ap.add_argument("--sky", choices=("day", "dusk", "night", "overcast"), default="day",
                    help="with --demo: the light and weather the plane view is drawn in")
    args = ap.parse_args()
    app = App(args)
    if args.screenshot:
        app.capture()
    else:
        app.run()


if __name__ == "__main__":
    main()
