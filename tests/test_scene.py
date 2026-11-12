"""The plane view's sky and ground: python tests/test_scene.py"""
import os
import sys
import time

os.environ["SDL_VIDEODRIVER"] = "dummy"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame  # noqa: E402

from overhead.feeds import DEMO_SKIES, DEMO_SKY  # noqa: E402
from overhead.scene import Scene, sky_colours, tone  # noqa: E402
from overhead.sky import Sky  # noqa: E402

pygame.display.init()
pygame.display.set_mode((1, 1))


def demo(name):
    return Sky(**{**DEMO_SKY, **DEMO_SKIES[name]})


def lum(c):
    return (c[0] + c[1] + c[2]) / 3


day, dusk, night, overcast = (demo(k) for k in ("day", "dusk", "night", "overcast"))

# colours: blue overhead by day, dark at night, orange at the horizon at sunset, grey under cloud
top, bottom = sky_colours(40.0)
assert top[2] > top[0] * 2 and lum(bottom) > lum(top), (top, bottom)
top, bottom = sky_colours(-30.0)
assert lum(top) < 12 and lum(bottom) < 20, (top, bottom)
top, bottom = sky_colours(0.0)
assert bottom[0] > bottom[2] * 1.5, "a low sun reddens the horizon: %s" % (bottom,)
top, bottom = sky_colours(40.0, cloud=1.0)
assert max(top) - min(top) < 30, "an overcast sky is grey: %s" % (top,)
for h in range(-40, 91, 5):
    for c in sky_colours(float(h), 0.5, 5000.0):
        assert all(0 <= v <= 255 for v in c), (h, c)

# the light on the aircraft follows the sky
amb_d, dir_d, src_d, key_d = tone(day)
amb_n, dir_n, src_n, key_n = tone(night)
amb_o, dir_o, _, key_o = tone(overcast)
amb_s, dir_s, _, key_s = tone(dusk)
assert lum(amb_d) > lum(amb_n) * 2.5 and lum(dir_d) > lum(dir_n) * 3
assert lum(dir_o) < lum(dir_d) * 0.5, "cloud takes away the direct sun"
assert amb_n[2] > amb_n[0], "what is left at night is blue"
assert len({key_d, key_n, key_o, key_s}) == 4
assert src_d == (day.sun_elev, day.sun_az) and src_n == (night.moon_elev, night.moon_az), "at night the moon lights it"
assert lum(tone(Sky(sun_elev=-30.0, moon_elev=-20.0))[0]) > 0.1, "never so dark that the aircraft can't be seen"

# what counts as a different sky
assert Scene.state(day) == Scene.state(Sky(**{**DEMO_SKY, "sun_elev": 38.3, "sun_az": 120.4}))
assert Scene.state(day) != Scene.state(dusk) != Scene.state(night)
assert Scene.state(day) != Scene.state(overcast)
assert Scene.state(Sky(sun_elev=40.0, known=False, cloud_total=0.9)) == Scene.state(Sky(sun_elev=40.0, known=False)), \
    "with no weather the sky is drawn clear"

# painting: the size of the Pi's window, quick enough to do in the background
W, H = 464, 216
sc = Scene(W, H)
assert sc.ppd == H / 48.0 and sc.pw == round(360 * sc.ppd)
win = pygame.Surface((W, H), 0, 32)
sc.window(win, 100.0, 30.0)
assert win.get_at((5, 5))[:3] == tuple(sc.flat), "before anything is painted the window is one colour"
t0 = time.perf_counter()
sc.pano = sc.paint(day)
took = time.perf_counter() - t0
assert sc.pano.get_size() == (sc.pw, sc.ph) and took < 3.0, took


def at(sky_pano, az, elev):
    sc.pano = sky_pano
    sc.window(win, az, elev)
    return win.get_at((W // 2, H // 2))[:3]


pano_day = sc.pano
up = at(pano_day, 300.0, 60.0)
down = at(pano_day, 300.0, -30.0)
assert up[2] > up[0] + 40, "day sky is blue: %s" % (up,)
assert down[1] > down[2], "the ground is green or brown, not sky: %s" % (down,)
sun = at(pano_day, day.sun_az, day.sun_elev)
assert lum(sun) > 235, "the sun is where it should be: %s" % (sun,)
assert lum(at(pano_day, day.sun_az + 180, day.sun_elev)) < 215

# the seam: looking due north shows the same picture whichever way the azimuth is written
sc.window(win, 0.0, 10.0)
a = pygame.image.tostring(win, "RGB")
sc.window(win, 360.0, 10.0)
assert a == pygame.image.tostring(win, "RGB")
sc.window(win, -0.2, 10.0)
left = win.subsurface((0, 0, W // 2 - 4, H)).copy()
sc.window(win, 359.8, 10.0)
assert pygame.image.tostring(left, "RGB") == pygame.image.tostring(win.subsurface((0, 0, W // 2 - 4, H)), "RGB")
for elev in (-89.0, 89.0, 200.0, -200.0):               # never reads outside the panorama
    sc.window(win, 123.0, elev)

pano_night = sc.paint(night)
assert lum(at(pano_night, 300.0, 60.0)) < 30, "night sky is dark"
moon = at(pano_night, night.moon_az, night.moon_elev)
assert lum(moon) > 120, "the moon is up where it should be: %s" % (moon,)
pano_dusk = sc.paint(dusk)
west = at(pano_dusk, dusk.sun_az, 3.0)
east = at(pano_dusk, dusk.sun_az + 180, 3.0)
assert west[0] > east[0] + 30 and west[0] > west[2], "sunset glows on the sun's side: %s %s" % (west, east)
grey = at(sc.paint(overcast), 300.0, 60.0)
assert max(grey) - min(grey) < 40, "overcast: %s" % (grey,)

# the same sky paints the same picture, so a repaint never flickers
assert pygame.image.tostring(sc.paint(dusk), "RGB") == pygame.image.tostring(pano_dusk, "RGB")

# asking for a sky returns at once and the picture arrives on its own
sc2 = Scene(W, H)
t0 = time.perf_counter()
sc2.request(night)
assert time.perf_counter() - t0 < 0.05 or sc2.pano is None
sc2.request(night)                                      # asking again while it paints starts nothing new
sc2.wait()
assert sc2.pano is not None and sc2.key == Scene.state(night) and sc2.take_fresh() and not sc2.take_fresh()
sc2.request(night)
assert not sc2._busy, "nothing to do when the sky hasn't changed"
sc2.request(day)
sc2.wait()
assert sc2.key == Scene.state(day) and sc2.take_fresh()
print("panorama %d x %d painted in %.0f ms" % (sc.pw, sc.ph, took * 1000))
print("ok")
