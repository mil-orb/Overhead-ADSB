"""Drives the app headless: rotation, taps, settings, Wi-Fi join, location. Run: python tests/test_ui.py"""
import json
import os
import sys
import time
import types

os.environ["SDL_VIDEODRIVER"] = "dummy"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pygame  # noqa: E402

from overhead import app as main  # noqa: E402
from overhead import wifi  # noqa: E402

wifi.get_wifi = lambda real=True: wifi.MockWifi()     # never the machine's own networks

main.CONFIG = os.path.join(ROOT, "data", "test_config.json")
os.makedirs(os.path.dirname(main.CONFIG), exist_ok=True)
with open(main.CONFIG, "w") as f:           # a wide range so the demo has plenty of aircraft to rotate through
    f.write('{"range_mi": 40, "output": "window"}')      # "window": on a Pi, never the real panel or its touch
# demo=True: setting a location below must not switch to live traffic or fetch ground pictures.
args = types.SimpleNamespace(demo=True, fullscreen=False, size="480x320", lat=None, lon=None,
                             screenshot=None, at="1", settings=None, fb=False, calibrate=False)
app = main.App(args)
W, H = app.W, app.H
b = app.board
assert app.unset and app.demo, "no location yet, so it should fall back to demo traffic"
assert b.U >= 36, "flaps should be large on a 480x320 screen, got %d px" % b.U
assert len(b.modules) < 50
bottom = max(m.rect.bottom for m in b.modules)
assert H - bottom < 40, "the card should fill the screen, %d px left under it" % (H - bottom)


def ev(kind, pos):
    app.event(pygame.event.Event(kind, pos=pos, button=1, buttons=(1, 0, 0), rel=(0, 0)))


def frames(n, dt=1 / 30):
    for _ in range(n):
        if app.settings.visible:
            if app.settings.update(dt):
                app.settings.draw(app.soft_dim())
            if app.settings.closed_now:
                app.after_settings()
        elif app.view.visible:
            app.tick_view(dt)
        else:
            app.step(dt)


def tap(pos):
    ev(pygame.MOUSEBUTTONDOWN, pos)
    ev(pygame.MOUSEBUTTONUP, pos)
    frames(2)


def wait(cond, limit=6.0):
    end = time.time() + limit
    while not cond() and time.time() < end:
        frames(1)
        time.sleep(0.02)
    assert cond(), "timed out"


def shown(key):
    mods, _ = b.cells[key]
    return "".join(list(m.fs.keys)[m.tgt] for m in mods).strip()


wait(lambda: app.engine.latest().stamp > 0)
frames(5)
deck = app.frame.sightings
demo_frame = app.frame
assert len(deck) >= 5
assert app.cur == deck[0].hex and deck[0].state == "view", "starts on the nearest visible aircraft"
assert shown("flight") == deck[0].plane.flight
assert b.count.text == "1/%d" % min(9, len(deck[:8])) and not b.count.blink, b.count.text

# tap right: next aircraft, and it holds there instead of rotating away
tap((W * 0.8, H // 2))
assert app.cur == deck[1].hex and shown("flight") == deck[1].plane.flight
assert app.hold_until > app.now() + 10
assert b.count.text.startswith("2/"), b.count.text
# tap left: previous
tap((W * 0.1, H // 2))
assert app.cur == deck[0].hex

# the touch tips: the tap that puts them away does nothing else. They show once per boot of the
# computer: tapped away, they stay away through restarts of the app until it boots again.
before = app.cur
app.boot = "boot-one"
assert app.tips_due()
app.show_hints()
assert app.hints_up
tap((W * 0.8, H // 2))
assert not app.hints_up and app.cur == before, "closing the tips must not step the card"
with open(main.CONFIG) as f:
    assert json.load(f)["hints_boot"] == "boot-one"
assert not app.tips_due(), "not again until the next boot"
app.boot = "boot-two"
assert app.tips_due(), "the next boot shows them again"
app.boot = None
assert app.tips_due(), "with no boot id (Windows, macOS), every start"
app.show_hints()
tap((W // 2, H // 2))
assert not app.hints_up and not app.view.visible, "nor open the plane view"

# automatic rotation: once the hold and the dwell have passed it moves on by itself
app.hold_until = 0
app.since = app.now() - 100
app._apply_at = 0
frames(2)
assert app.cur == app.frame.sightings[1].hex

# a military aircraft that turns up interrupts the rotation
mil = next(s for s in app.frame.sightings if s.plane.military)
app.known.discard(mil.hex)
app.cur, app.since, app._apply_at = app.frame.sightings[0].hex, app.now() - 5, 0
frames(2)
assert app.cur == mil.hex, "military should jump the queue"
assert b.remark.tgt == b.remark.fs.index("MILITARY") and b.mil.mode == 2

# press and hold locks onto the aircraft showing: no rotation, the counter blinks, numbers keep moving
from overhead.tracker import Frame  # noqa: E402
app.hold_until = 0
app.toggle_lock()
frames(2)
locked = app.cur
assert app.lock == locked
assert b.count.blink, "the counter blinks while locked"
app.since = app.now() - 500
app._apply_at = 0
frames(3)
assert app.cur == locked, "a locked aircraft is not rotated away"
s0 = app.frame.find_any(locked)
near, later = app.live(s0, app.frame, app.now()), app.live(s0, app.frame, app.now() + 20)
assert abs(near.rng - later.rng) > 500, "20 seconds of flight should change the distance"
assert later.state == s0.state, "the visibility verdict stays the tracker's"
# it stays up when it drops out of the rotation but is still in range
fr = app.frame
gone = Frame(sightings=[s for s in fr.sightings if s.hex != locked], everyone=fr.everyone, sky=fr.sky, stamp=fr.stamp)
app.apply(gone, app.now())
assert app.cur == locked and shown("flight") == s0.plane.flight[:7]
# and is let go once it has left the radius
left = Frame(sightings=gone.sightings, everyone=[s for s in fr.everyone if s.hex != locked], sky=fr.sky, stamp=fr.stamp)
app.apply(left, app.now())
assert app.lock is None and app.cur == left.sightings[0].hex
# press and hold again lets go
app.toggle_lock()
assert app.lock == app.cur
app.toggle_lock()
assert app.lock is None
# follow mode: whatever comes up is locked, so it never rotates
app.cfg["follow"] = True
app.apply(left, app.now())
assert app.lock == app.cur
app.since = app.now() - 500
app.apply(left, app.now())
assert app.cur == left.sightings[0].hex
app.cfg["follow"] = False
app.lock = None

# a short drag in the upper half is neither a tap nor a settings swipe
before = app.cur
ev(pygame.MOUSEBUTTONDOWN, (240, 60))
ev(pygame.MOUSEMOTION, (240, 20))
ev(pygame.MOUSEBUTTONUP, (240, 20))
assert not app.settings.visible and app.cur == before

# swipe up from the lower part of the screen opens settings
st = app.settings
ev(pygame.MOUSEBUTTONDOWN, (240, H - 40))
ev(pygame.MOUSEMOTION, (240, H - 90))
assert st.visible and st.opening
rects = st.draw(0.0)
assert abs(st.off - (H - 50)) < 1, "the sheet's edge sits under the finger"
assert rects and max(r.bottom for r in rects) - min(r.top for r in rects) <= 50 + st.hh + st.edge_h + 2, rects
assert app.scr.get_at((5, 5)) == st.backdrop.get_at((5, 5)), "the board still shows above the edge"
ev(pygame.MOUSEMOTION, (240, H - 150))
st.draw(0.0)
ev(pygame.MOUSEBUTTONUP, (240, H - 150))
bands, steps = [], 0
while st.off > 0 and steps < 60:
    if st.update(1 / 30):
        rr = st.draw(0.0)
        if rr:
            bands.append(max(r.bottom for r in rr) - min(r.top for r in rr))
    steps += 1
assert st.off == 0 and st.page == "main"
assert 6 <= steps <= 14, "about a third of a second at 30 fps, got %d frames" % steps
assert max(bands) <= H * 0.45 and sum(bands) <= H * 2.2, (max(bands), sum(bands))
assert all(app.scr.get_at(q) == st.sheet.get_at(q) for q in ((5, 5), (240, 160), (470, 310))), "ends as the full sheet"
print("sheet opened in %d frames; tallest repaint %d lines, %d lines in total" % (steps, max(bands), sum(bands)))
# a single setting changing repaints just that control
st.update(1 / 30)
st.draw(0.0)
st._set("night_dim", not app.cfg["night_dim"])
st.update(1 / 30)
rr = st.draw(0.0)
assert rr and max(r.h for r in rr) <= st.rh and max(r.w for r in rr) < W // 2, rr
st._set("night_dim", not app.cfg["night_dim"])
frames(2)
assert st.rh >= 56, "rows sized for a resistive screen"


def row_y(i):
    return st.hh + i * st.rh - st.scroll["main"] + st.rh // 2


def segs(i):
    y = st.hh + i * st.rh - st.scroll["main"]
    return [r for r, fn, inst in st.hits if r.y == y and r.h == st.rh and r.w < W // 2]


# units -> KM
tap(segs(5)[1].center)
assert app.cfg["units"] == "km"
# scroll a little so the range row is on screen
ev(pygame.MOUSEBUTTONDOWN, (100, 300))
ev(pygame.MOUSEMOTION, (100, 200))
ev(pygame.MOUSEBUTTONUP, (100, 200))
frames(2)
# range -> 10 miles; the label follows the distance unit (KM now)
assert any(t[0] == "RANGE KM" for t in st._t), "range row is labelled in the chosen unit"
tap(segs(6)[1].center)
assert app.cfg["range_mi"] == 10, app.cfg["range_mi"]
# brightness slider drag
x1, x2 = st.slider
ev(pygame.MOUSEBUTTONDOWN, (x2, st.slider_rect.centery))
ev(pygame.MOUSEMOTION, ((x1 + x2) // 2, st.slider_rect.centery))
ev(pygame.MOUSEBUTTONUP, ((x1 + x2) // 2, st.slider_rect.centery))
frames(2)
assert 0.5 < app.cfg["brightness"] < 0.65, app.cfg["brightness"]
# scroll down and set how long each plane stays up
ev(pygame.MOUSEBUTTONDOWN, (100, 300))
ev(pygame.MOUSEMOTION, (100, 10))
ev(pygame.MOUSEBUTTONUP, (100, 10))
frames(2)
assert st.scroll["main"] > 0
tap(segs(9)[2].center)
assert app.cfg["dwell_s"] == 15, app.cfg["dwell_s"]
st.scroll["main"] = 0
st.dirty = True
frames(1)

# show: open the mode list, pick OVERHEAD, land back on the main list with it shown
tap((100, row_y(2)))
assert st.page == "show"
frames(1)
rows = sorted((r for r, fn, inst in st.hits if r.w == W and r.h == round(60 * st.s)), key=lambda r: r.y)
assert len(rows) >= 4, len(rows)
tap(rows[1].center)
assert app.cfg["mode"] == "overhead" and st.page == "main"
frames(1)
assert any(t[0] == "OVERHEAD" for t in st._t), "the main list shows the chosen mode"

# wifi: open page, scan, pick a secured network, type a password, join
tap((100, row_y(0)))
assert st.page == "wifi"
wait(lambda: st.wifi_busy is None and st.nets)
row = next(i for i, n in enumerate(st.nets) if n.ssid == "Workshop")
tap((150, st.hh + round(30 * st.s) + row * st.rh + st.rh // 2))
assert st.page == "key", st.page


def press(ch):
    frames(1)
    for r, fn, inst in st.hits:
        if inst and fn.__defaults__ and fn.__defaults__[0] == ch:
            tap(r.center)
            return
    raise AssertionError("no key " + ch)


for ch in "hunter22":
    if ch.isdigit() and st.kb_mode != "123":
        st._mode()
    if ch.isalpha() and st.kb_mode not in ("abc", "ABC"):
        st._mode()
    press(ch)
assert st.kb_text == "hunter22", st.kb_text
st._submit()
frames(1)
assert st.page == "wifi" and st.wifi_busy == "join"
wait(lambda: st.wifi_busy is None)
assert st.ssid == "Workshop" and st.wifi_msg[0] == "CONNECTED", st.wifi_msg
# back, then location
tap((20, st.hh // 2))
assert st.page == "main"
tap((100, row_y(1)))
assert st.page == "loc"
for i, text in enumerate(("40.6413", "-73.7781")):
    st._focus(i)
    for ch in text:
        st._loc_key(ch)
assert st._loc_values() == (40.6413, -73.7781)
st._loc_save()
frames(1)
assert st.page == "main" and app.cfg["lat"] == 40.6413
# DONE closes; changes are saved and applied
done = next(r for r, fn, inst in st.hits if r.right >= W - 2 and r.y == 0)
tap(done.center)
frames(20)
assert not st.visible
assert app.board.unit_label == "KM" and not app.unset
assert app.demo and app.ground is None, "a test never goes online"
assert abs(app.engine.obs.range_m - 10 * 1609.344) < 1, "the new range reached the tracker"
assert app.engine.tracker.mode == "overhead", "the new mode reached the tracker"
saved = json.load(open(main.CONFIG))
assert saved["mode"] == "overhead" and saved["range_mi"] == 10 and saved["units"] == "km" and saved["lat"] == 40.6413 and saved["wifi_ssid"] == "Workshop" and saved["dwell_s"] == 15
app.engine.stop()
os.remove(main.CONFIG)

# the plane view: tap the middle of the card to open it, drag to look around, tap to close
def view_frames(n, dt=1 / 30):
    for _ in range(n):
        app.tick_view(dt)


app.frame, app._apply_at = demo_frame, 0
app.engine.frame = demo_frame            # what the view asks for when it refreshes its numbers
app.cur = demo_frame.sightings[0].hex
view = app.view
tap((W // 2, H // 2))
assert view.visible and app.view_hex == app.cur, "a tap in the middle opens the plane view"
assert view.scene._busy or view.scene.pano is not None, "the sky behind it is being painted, off the main loop"
assert view._rough, "it slides in with the quick 1:1 picture"
view.scene.wait()
bands = []
for _ in range(30):
    rr = app.tick_view(1 / 30)
    if rr and 0 < view.off < H:
        bands.append(max(r.bottom for r in rr) - min(r.top for r in rr))
assert view.off == H, view.off
assert not view._rough, "once it stops, the smooth picture replaces the quick one"
assert bands and max(bands) < H * 0.6, "the wipe repaints a band, not the screen: %s" % bands
info = view.info
assert info["type"] == demo_frame.sightings[0].plane.type and info["up"].endswith("UP")
assert app.board.unit_label in info["dist"], info["dist"]
assert app.scr.get_at((W // 2, view.scope.centery)) != app.scr.get_at((3, 3)), "something is drawn in the scope"
view.set(view.info)                      # the painted sky has arrived
assert app.tick_view(1 / 30), "the scope is redrawn with the sky"
row = [app.scr.get_at((x, view.scope.y + view.scope.h // 4)) for x in range(view.scope.x + 4, view.scope.right - 4, 6)]
blue = sum(1 for c in row if c[2] > c[0] + 20)
assert blue > len(row) // 2, "daytime in the demo: blue sky behind the aircraft (%d of %d)" % (blue, len(row))
quiet = app.tick_view(1 / 30)
assert quiet == [], "nothing repaints while the aircraft's attitude hasn't changed"
ev(pygame.MOUSEBUTTONDOWN, (200, 150))
ev(pygame.MOUSEMOTION, (260, 130))
assert view.yaw != 0 and view.tilt != 0, "dragging looks around the aircraft"
assert app.tick_view(1 / 30), "and the picture is redrawn"
ev(pygame.MOUSEBUTTONUP, (260, 130))
assert view.visible, "a drag does not close it"
view_frames(20)
assert view.yaw == 0 and view.tilt == 0, "letting go swings back to the real view"
tap_pos = (240, 160)
ev(pygame.MOUSEBUTTONDOWN, tap_pos)
ev(pygame.MOUSEBUTTONUP, tap_pos)
view_frames(30)
assert not view.visible, "a tap closes it"
assert app.hold_until > app.now(), "and the card stays on that aircraft for a moment"
frames(3)


# "slide" motion (whole sheet travels) still works for fast displays
st = app.settings
st.motion = "slide"
st.open(app.scr.copy())
mid = None
for _ in range(20):
    if st.update(1 / 30):
        rr = st.draw(0.0)
        if 0 < st.off < H:
            mid = (int(st.off), rr)
assert st.off == 0 and mid and mid[1][0].h == H, "slide repaints the whole screen each frame"
assert app.scr.get_at((240, 160)) == st.sheet.get_at((240, 160))
st.close()
frames(20)
st.motion = "reveal"
assert not st.visible

# nothing to show: every flap goes blank and the top lamp turns red
from overhead.tracker import Frame  # noqa: E402
b = app.board
frames(120)
app.apply(Frame(sightings=[], stamp=1.0, in_range=0, online=True), app.now())
assert all(list(m.fs.keys)[m.tgt] in (" ", "") for m in b.modules if m not in b.clock), "all flaps blank"
assert b.view.on is b.view_red and b.view.mode == 1 and b.mil.mode == 0, "red lamp, steady"
assert b.count.text == "", b.count.text
# feed lost: still blank, red lamp blinks, and the text line says why
app.apply(Frame(sightings=[], stamp=1.0, online=False), app.now())
assert b.view.on is b.view_red and b.view.mode == 3
assert shown("flight") == "NO DATA"
assert all(list(m.fs.keys)[m.tgt] == " " for k, (mods, _) in b.cells.items() if k != "flight" for m in mods)
# a plane comes back: lamp returns to green
app.cur = None
app.apply(demo_frame, app.now())
assert b.view.on is b.view_green and b.view.mode == 1

# timing: worst case, every module flipping at once, and how much of the screen that repaints
b = app.board
for m in b.modules:
    m.set((m.tgt + m.fs.n // 2) % m.fs.n)
t0 = time.perf_counter()
n = 0
area = 0
while b.active and n < 400:
    rects = b.update(1 / 30)
    if rects:
        top, bottom = min(r.top for r in rects), max(r.bottom for r in rects)
        area = max(area, bottom - top)
    n += 1
ms = (time.perf_counter() - t0) / max(1, n) * 1000
print("flap %dx%d px; %d modules; fall %.0f ms at %.0f flaps/s; worst-case frame %.2f ms on this PC; "
      "tallest repaint band %d of %d lines" % (b.U, b._ch(b.U), len(b.modules), b.fall * 1000, b.rate, ms, area, H))
print("ok")
