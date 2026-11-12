"""Aircraft meshes and the plane-view renderer. Run: python tests/test_models.py"""
import json
import math
import os
import sys

os.environ["SDL_VIDEODRIVER"] = "dummy"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pygame  # noqa: E402

from overhead import hangar, models  # noqa: E402

pygame.display.init()
pygame.display.set_mode((1, 1))

# every type in the table builds, with sane geometry and its real proportions
for code in models.TYPES:
    kind, name, length, span, radius, opts = models.TYPES[code]
    m = models.helicopter(name, length, span, radius, **opts) if kind == "H" else models.airplane(name, length, span, radius, **opts)
    assert m.points and m.faces, code
    assert all(math.isfinite(v) for p in m.points for v in p), code
    assert all(0 <= i < len(m.points) for f in m.faces for i in f[0]), code
    assert all(abs(math.sqrt(sum(c * c for c in f[3])) - 1) < 1e-6 for f in m.faces if len(f[0]) >= 3), code
    ys = [p[1] for p in m.points]
    assert abs((max(ys) - min(ys)) - span) < span * 0.12, (code, max(ys) - min(ys), span)
    assert len(m.faces) <= 400, (code, len(m.faces))

# lookups: exact, by family, by category, and the last resort
assert models.spec_for("C172") == ("C172", True)
assert models.spec_for("B73Q") == ("B738", False)
assert models.spec_for("ZZZZ", "A7") == ("EC35", False)
assert models.spec_for("", "") == ("B738", False)
assert models.spec_for("../../etc/passwd")[1] is False
assert models._converted("../x") is None and models._converted("a b") is None, "only plain type codes reach the disk"

# converted open-source models: every file loads, stays small, and carries its licence and credit
files = sorted(f for f in os.listdir(models.MODEL_DIR) if f.endswith(".json"))
assert len(files) >= 30, len(files)
for f in files:
    path = os.path.join(models.MODEL_DIR, f)
    j = json.load(open(path, encoding="utf-8"))
    assert j["licence"] in ("GPL-2.0", "CC-BY-4.0") and j["credit"] and j["source"], f
    assert os.path.getsize(path) < 40000 and len(j["tris"]) <= 1250, (f, os.path.getsize(path), len(j["tris"]))
    m = models._converted(f[:-5])
    assert m and len(j["tris"]) * 0.93 <= len(m.faces) <= len(j["tris"]) and m.credit.startswith("MODEL: "), f
    ys = [p[1] for p in m.points]
    assert abs((max(ys) - min(ys)) - m.span) < 0.05, f
    assert f[:-5] in models.TYPES, f
    assert j["solid"] and all(face[2] for face in m.faces), "%s is not one closed skin, so it would draw with holes" % f
    assert len(m.lit) == len(m.faces), f
    assert all(abs(math.sqrt(sum(c * c for c in n)) - 1) < 1e-6 for n in m.lit), f
    agree = sum(1 for face, n in zip(m.faces, m.lit) if sum(a * b for a, b in zip(face[3], n)) > 0.5)
    assert agree > len(m.faces) * 0.97, "%s: smoothed normals should stay close to the real ones" % f
    left, right, tail = m.lamps
    assert m.points[left][1] < -m.span * 0.4 and m.points[right][1] > m.span * 0.4, "%s: lights go on the wingtips" % f
    assert m.points[tail][0] < 0 and abs(m.points[tail][1]) < m.span * 0.12, "%s: the white light goes on the tail" % f
for code, (key, _) in models.RELATIVES.items():
    assert key + ".json" in files, "%s borrows %s, which has no file" % (code, key)
m738, same = models.model_for("B38M")
assert same and m738.credit.startswith("MODEL"), "a 737 MAX 8 draws the converted 737-800"
assert models.model_for("C17")[0].credit.startswith("SHAPE"), "no open model for a C-17: the generated shape is used"

# the pose: rows are perpendicular unit vectors, and the geometry is right way round
def dot(a, b):
    return sum(x * y for x, y in zip(a, b))

for args in ((0, 0, 0, 0, 0), (78, 3, 0, 150, 68), (300, -4, 20, 10, 5), (180, 0, 0, 180, 89)):
    r0, r1, r2 = hangar.pose(*args)
    for a in (r0, r1, r2):
        assert abs(dot(a, a) - 1) < 1e-9, args
    assert abs(dot(r0, r1)) < 1e-9 and abs(dot(r0, r2)) < 1e-9 and abs(dot(r1, r2)) < 1e-9, args
# Aircraft due north of you, low on the horizon, flying east: nose to the right, right wing toward you.
m = hangar.pose(90, 0, 0, 0, 0)
nose = tuple(row[0] for row in m)       # where the nose direction lands in view space (right, up, away)
assert nose[0] > 0.99, nose
wing = tuple(row[1] for row in m)       # its right wing points south, which is back at you
assert wing[2] < -0.99, wing
# Straight overhead you are looking at its belly: the model's "up" points away from you.
m = hangar.pose(0, 0, 0, 0, 89)
up = tuple(row[2] for row in m)
assert up[2] > 0.99, up
# Climbing raises the nose on screen; a right bank drops the right wing
assert hangar.pose(90, 10, 0, 0, 0)[1][0] > 0.15
assert hangar.pose(0, 0, 25, 180, 0)[1][1] < -0.3

# rendering draws something, skips hidden faces of closed models, and stays inside the picture
low = pygame.Surface((232, 107))
shades = {}
for code in ("B738", "A320", "C172", "EC35", "C17", "H47"):
    model, _ = models.model_for(code)
    low.fill((0, 0, 0))
    n = hangar.render(low, model, hangar.pose(78, 2, 0, 150, 60), hangar.livery("UAL1", False, model.kind),
                      (0.3, 0.6, -0.7), 116, 53, 0.46 * 107 * 1.55 / model.radius, shades)
    assert 0 < n <= len(model.faces), code
    box = low.get_bounding_rect()
    assert box.w > 60 and box.h > 20, (code, box)
    if all(f[2] for f in model.faces):
        assert n < len(model.faces) * 0.75, "closed skins should cull their far side: %s %d of %d" % (code, n, len(model.faces))
assert shades and all(len(c) == 3 and all(0 <= v <= 255 for v in c) for c in shades.values())
# the same aircraft under night light is much darker, and its lights show
model, _ = models.model_for("B738")
pose = hangar.pose(78, 2, 0, 150, 60)
scale = 0.46 * 107 * 1.55 / model.radius


def brightness(lighting):
    low.fill((0, 0, 0))
    hangar.render(low, model, pose, hangar.livery("UAL1", False, model.kind), (0.3, 0.6, -0.7), 116, 53, scale, shades, lighting)
    px = [low.get_at((x, y))[:3] for x in range(0, 232, 3) for y in range(0, 107, 3)]
    lit = [sum(p) for p in px if sum(p)]
    return sum(lit) / len(lit)


assert brightness(hangar.DAYLIGHT) > 3 * brightness(((0.1, 0.11, 0.15), (0.0, 0.0, 0.0), "dark"))
low.fill((0, 0, 0))
hangar.lamps(low, model, pose, 116, 53, scale * 0.6, 1, 1.0)
reds = sum(1 for x in range(232) for y in range(107) if low.get_at((x, y))[0] > 200 and low.get_at((x, y))[1] < 80)
greens = sum(1 for x in range(232) for y in range(107) if low.get_at((x, y))[1] > 200 and low.get_at((x, y))[0] < 80)
assert reds and greens, "red and green wingtip lights"
assert hangar.livery("UAL328", False, "jet")["tail"] != hangar.livery("DAL12", False, "jet")["tail"]
assert hangar.livery("RCH871", True, "jet")["body"] == hangar.MILITARY["body"]
print("%d generated types, %d converted models" % (len(models.TYPES), len(files)))
print("ok")
