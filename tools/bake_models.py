"""Turn detailed open-source aircraft models into the small flat-coloured meshes the Pi draws.

Runs inside Blender (tested with 5.2), which does the polygon reduction:

    blender -b --factory-startup --python tools/bake_models.py -- SRC_DIR OUT_DIR [key ...]

SRC_DIR holds the downloaded .glb files (see models/README.md for where they come from).
Each one is read here, not with Blender's importer, because the Flightradar24 set is in
glTF 1.0, which Blender no longer opens. For every triangle the colour is taken from the
model's texture at that spot, so the paint scheme survives without shipping any textures.
The mesh is then welded, cleaned of small loose parts (wheels, antennas), reduced to about
TARGET triangles, turned to nose-forward / right-wing / up axes, scaled to the type's real
wingspan and written as JSON of a few kilobytes.

The outputs are derived works of the sources and keep their licences.
"""
import json
import math
import os
import re
import struct
import sys
import tempfile

import bmesh
import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from overhead.models import TYPES  # noqa: E402  (real dimensions and names per type)

TARGET = 1000          # triangles for a closed skin, half of which face away and are skipped
TARGET_OPEN = 600      # for open shells, the last resort, which are drawn in full
GLASS = (0.11, 0.16, 0.24)

FR24 = "FLIGHTGEAR / FLIGHTRADAR24, GPL-2.0"
AMV = "AMVLAB AIRCRAFT-MODELS, CC BY 4.0"
# output key (an ICAO type code) -> (source file, credit line, licence)
# Flightradar24's 787-8 and A350 are left out: amvlab's are cleaner.
SOURCES = {
    "A318": ("fr24/models/a318.glb", FR24, "GPL-2.0"), "A319": ("fr24/models/a319.glb", FR24, "GPL-2.0"),
    "A320": ("fr24/models/a320.glb", FR24, "GPL-2.0"), "A321": ("fr24/models/a321.glb", FR24, "GPL-2.0"),
    "A332": ("fr24/models/a332.glb", FR24, "GPL-2.0"), "A333": ("fr24/models/a333.glb", FR24, "GPL-2.0"),
    "A343": ("fr24/models/a343.glb", FR24, "GPL-2.0"), "A346": ("fr24/models/a346.glb", FR24, "GPL-2.0"),
    "A359": ("amvlab/models/A350_nologo.glb", AMV, "CC-BY-4.0"), "A388": ("fr24/models/a380.glb", FR24, "GPL-2.0"),
    "AT45": ("fr24/models/atr42.glb", FR24, "GPL-2.0"), "B737": ("fr24/models/b737.glb", FR24, "GPL-2.0"),
    "B738": ("fr24/models/b738.glb", FR24, "GPL-2.0"), "B739": ("fr24/models/b739.glb", FR24, "GPL-2.0"),
    "B744": ("fr24/models/b744.glb", FR24, "GPL-2.0"), "B748": ("fr24/models/b748.glb", FR24, "GPL-2.0"),
    "B752": ("fr24/models/b752.glb", FR24, "GPL-2.0"), "B753": ("fr24/models/b753.glb", FR24, "GPL-2.0"),
    "B762": ("fr24/models/b762.glb", FR24, "GPL-2.0"), "B763": ("fr24/models/b763.glb", FR24, "GPL-2.0"),
    "B764": ("fr24/models/b764.glb", FR24, "GPL-2.0"), "B772": ("fr24/models/b772.glb", FR24, "GPL-2.0"),
    "B773": ("fr24/models/b773.glb", FR24, "GPL-2.0"), "B788": ("amvlab/models/B787_nologo.glb", AMV, "CC-BY-4.0"),
    "CRJ9": ("fr24/models/crj900.glb", FR24, "GPL-2.0"), "CRJ7": ("fr24/models/crj700.glb", FR24, "GPL-2.0"),
    "B789": ("fr24/models/b789.glb", FR24, "GPL-2.0"), "C560": ("fr24/models/citation.glb", FR24, "GPL-2.0"),
    "BCS1": ("fr24/models/cs100.glb", FR24, "GPL-2.0"), "BCS3": ("fr24/models/cs300.glb", FR24, "GPL-2.0"),
    "E170": ("fr24/models/e170.glb", FR24, "GPL-2.0"), "E190": ("fr24/models/e190.glb", FR24, "GPL-2.0"),
    "EC35": ("fr24/models/heli.glb", FR24, "GPL-2.0"), "P28A": ("fr24/models/pa28.glb", FR24, "GPL-2.0"),
    "DH8D": ("fr24/models/q400.glb", FR24, "GPL-2.0"), "GLID": ("fr24/models/ask21.glb", FR24, "GPL-2.0"),
}
SKIP = re.compile(r"interior|wire|antenn|pitot|light|lts|lens|beacon|strobe|wiper|hinge|hook|handle|buckle|pitchlink|blur"
                  r"|disc|ratblades|^rat$|well|chrome|heater|trous|projo|lampe|^bol$|floats|carrier|radom")
COMPONENTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
DTYPES = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}


class Gltf:
    """Just enough of glTF 1.0 and 2.0 to get triangles, texture coordinates and base colours."""

    def __init__(self, path):
        data = open(path, "rb").read()
        magic, self.version, _ = struct.unpack_from("<4sII", data, 0)
        if magic != b"glTF":
            raise ValueError("not a binary glTF file")
        if self.version == 1:
            clen, _ = struct.unpack_from("<II", data, 12)
            self.j = json.loads(data[20:20 + clen])
            self.body = data[20 + clen:]
        else:
            off, self.body = 12, b""
            while off < len(data):
                clen, ctype = struct.unpack_from("<II", data, off)
                chunk = data[off + 8:off + 8 + clen]
                if ctype == 0x4E4F534A:
                    self.j = json.loads(chunk)
                elif ctype == 0x004E4942:
                    self.body = chunk
                off += 8 + clen
        self._images = {}

    def get(self, kind, ref):
        return self.j[kind][ref]

    def accessor(self, ref):
        a = self.get("accessors", ref)
        view = self.get("bufferViews", a["bufferView"])
        dt = np.dtype(DTYPES[a["componentType"]])
        n = COMPONENTS[a["type"]]
        off = view.get("byteOffset", 0) + a.get("byteOffset", 0)
        stride = a.get("byteStride") or view.get("byteStride") or dt.itemsize * n
        return np.ndarray((a["count"], n), dt, self.body, off, (stride, dt.itemsize)).copy()

    def image(self, ref):
        """Pixels of an embedded image as an (h, w, 4) float array, row 0 at the bottom."""
        if ref in self._images:
            return self._images[ref]
        im = self.get("images", ref)
        view_ref = im.get("bufferView")
        mime = im.get("mimeType", "")
        ext = im.get("extensions", {}).get("KHR_binary_glTF")
        if ext:
            view_ref, mime = ext["bufferView"], ext.get("mimeType", "")
        px = None
        if view_ref is not None:
            view = self.get("bufferViews", view_ref)
            raw = self.body[view.get("byteOffset", 0):view.get("byteOffset", 0) + view["byteLength"]]
            # Only PNG and JPEG are handed to Blender's image loader, from a private folder.
            kind = ".png" if raw[:8] == b"\x89PNG\r\n\x1a\n" else ".jpg" if raw[:3] == b"\xff\xd8\xff" else None
            if kind:
                with tempfile.TemporaryDirectory() as folder:
                    tmp = os.path.join(folder, "texture" + kind)
                    with open(tmp, "wb") as f:
                        f.write(raw)
                    img = bpy.data.images.load(tmp, check_existing=False)
                    img.colorspace_settings.name = "Non-Color"
                    w, h = img.size
                    if w and h:
                        px = np.empty(w * h * 4, np.float32)
                        img.pixels.foreach_get(px)
                        px = px.reshape(h, w, 4)
                    bpy.data.images.remove(img)
        self._images[ref] = px
        return px

    def material(self, ref):
        """(base colour, texture pixels or None, see-through?) for a material."""
        if ref is None:
            return (0.8, 0.8, 0.8), None, False
        m = self.get("materials", ref)
        if self.version == 1:
            values = m.get("values") or m.get("extensions", {}).get("KHR_materials_common", {}).get("values", {})
            d = values.get("diffuse", [0.8, 0.8, 0.8, 1.0])
            clear = float(values.get("transparency", 1.0)) < 0.7
            if isinstance(d, str):
                tex = self.get("textures", d)
                return (1.0, 1.0, 1.0), self.image(tex["source"]), clear
            return tuple(d[:3]), None, clear or (len(d) > 3 and d[3] < 0.7)
        pbr = m.get("pbrMetallicRoughness", {})
        base = pbr.get("baseColorFactor", [1, 1, 1, 1])
        clear = m.get("alphaMode") == "BLEND" and base[3] < 0.7
        tex = pbr.get("baseColorTexture")
        if tex is not None:
            return tuple(base[:3]), self.image(self.get("textures", tex["index"])["source"]), clear
        return tuple(base[:3]), None, clear

    def triangles(self):
        """Every triangle in the default scene: (positions (n,3,3), colours (n,3))."""
        scenes = self.j["scenes"]
        scene = scenes[self.j.get("scene", 0 if isinstance(scenes, list) else next(iter(scenes)))]
        tris, cols = [], []

        def local(node):
            if "matrix" in node:
                return np.array(node["matrix"], np.float64).reshape(4, 4).T
            m = np.eye(4)
            if "rotation" in node:
                x, y, z, w = node["rotation"]
                m[:3, :3] = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                             [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                             [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
            if "scale" in node:
                m[:3, :3] = m[:3, :3] * np.array(node["scale"])
            if "translation" in node:
                m[:3, 3] = node["translation"]
            return m

        def walk(ref, parent):
            node = self.get("nodes", ref)
            world = parent @ local(node)
            label = str(node.get("name") or ref).lower()
            skip = SKIP.search(label) is not None        # cabin interiors, lights, aerials and the like
            refs = node.get("meshes", []) if self.version == 1 else ([node["mesh"]] if "mesh" in node else [])
            for mref in ([] if skip else refs):
                for prim in self.get("meshes", mref)["primitives"]:
                    if prim.get("mode", 4) != 4 or "POSITION" not in prim["attributes"]:
                        continue
                    pos = self.accessor(prim["attributes"]["POSITION"]).astype(np.float64)
                    pos = pos @ world[:3, :3].T + world[:3, 3]
                    idx = self.accessor(prim["indices"]).reshape(-1) if "indices" in prim else np.arange(len(pos))
                    idx = idx[:len(idx) // 3 * 3].reshape(-1, 3)
                    base, tex, clear = self.material(prim.get("material"))
                    col = np.tile(np.array(base, np.float64), (len(idx), 1))
                    uvref = prim["attributes"].get("TEXCOORD_0")
                    if clear:
                        col[:] = GLASS
                    elif tex is not None and uvref is not None:
                        uv = self.accessor(uvref).astype(np.float64)[idx]           # (n, 3, 2)
                        h, w = tex.shape[:2]
                        samples = [uv.mean(axis=1)] + [(uv.mean(axis=1) + uv[:, k]) / 2 for k in range(3)]
                        acc = np.zeros((len(idx), 3))
                        for s in samples:
                            x = (np.floor(s[:, 0] * w).astype(int)) % w
                            y = (np.floor((1.0 - s[:, 1]) * h).astype(int)) % h
                            acc += tex[y, x, :3]
                        col = acc / len(samples) * np.array(base)
                    tris.append(pos[idx])
                    cols.append(col)
            for child in node.get("children", []):
                walk(child, world)

        for ref in scene["nodes"]:
            walk(ref, np.eye(4))
        return np.concatenate(tris), np.clip(np.concatenate(cols), 0, 1)


def axes(points):
    """Work out which way the model faces. Returns (nose, right, up) unit vectors in its own coordinates."""
    lo, hi = points.min(axis=0), points.max(axis=0)
    ext = hi - lo
    up_axis = int(np.argmin(ext))                           # aircraft are flatter than they are long or wide
    horiz = [a for a in range(3) if a != up_axis]
    centre = (lo + hi) / 2
    up = np.zeros(3)
    best = None
    for sign in (1, -1):
        # The fin is the highest thing on an aeroplane: few points, close to the centre line, at one end.
        h = points[:, up_axis] * sign
        top = points[h > np.quantile(h, 0.985)]
        spread = [(top[:, a].max() - top[:, a].min()) / ext[a] for a in horiz]
        offset = [abs(top[:, a].mean() - centre[a]) / ext[a] for a in horiz]
        score = max(offset) - min(spread)
        if best is None or score > best[0]:
            long_axis = horiz[int(np.argmax(offset))]
            tail_side = 1 if top[:, long_axis].mean() > centre[long_axis] else -1
            best = (score, sign, long_axis, tail_side)
    _, sign, long_axis, tail_side = best
    up[up_axis] = sign
    nose = np.zeros(3)
    nose[long_axis] = -tail_side
    return nose, np.cross(nose, up), up


def bake(key, src, credit, licence, out_dir):
    kind, name, length, span, radius, opts = TYPES[key]
    g = Gltf(src)
    tris, cols = g.triangles()

    # colours: 4 bits a channel, then keep the most used
    q = np.round(cols * 15).astype(int)
    code = q[:, 0] * 256 + q[:, 1] * 16 + q[:, 2]
    area = np.linalg.norm(np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0]), axis=1)
    codes, inverse = np.unique(code, return_inverse=True)
    weight = np.bincount(inverse, area)
    keep = codes[np.argsort(-weight)[:24]]
    palette = np.array([[c // 256, c // 16 % 16, c % 16] for c in keep]) / 15.0
    nearest = np.argmin(((q[:, None, :] / 15.0 - palette[None]) ** 2).sum(axis=2), axis=1)

    # into Blender for welding, cleaning and reduction
    bpy.ops.wm.read_factory_settings(use_empty=True)
    mesh = bpy.data.meshes.new(key)
    verts = tris.reshape(-1, 3)
    mesh.from_pydata(verts.tolist(), [], np.arange(len(verts)).reshape(-1, 3).tolist())
    for i in range(len(palette)):
        mesh.materials.append(bpy.data.materials.new("c%d" % i))
    mesh.polygons.foreach_set("material_index", nearest.astype(np.int32))
    obj = bpy.data.objects.new(key, mesh)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    size = float(np.ptp(verts, axis=0).max())

    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=size * 0.0006)
    # drop small loose parts: wheels, struts, aerials, wipers
    seen, doomed = set(), []
    for start in bm.verts:
        if start in seen:
            continue
        island, stack = [], [start]
        seen.add(start)
        while stack:
            v = stack.pop()
            island.append(v)
            for e in v.link_edges:
                o = e.other_vert(v)
                if o not in seen:
                    seen.add(o)
                    stack.append(o)
        co = np.array([v.co[:] for v in island])
        if np.ptp(co, axis=0).max() < size * 0.045:
            doomed += island
    if doomed:
        bmesh.ops.delete(bm, geom=doomed, context="VERTS")
    bm.to_mesh(mesh)
    bm.free()

    before = len(mesh.polygons)
    # Where each colour is on the original surface, so it can be put back after the geometry is rebuilt.
    soup = np.arange(len(verts)).reshape(-1, 3).tolist()
    paint = BVHTree.FromPolygons([Vector(v) for v in verts], soup)
    centres = tris.mean(axis=1)
    probe = centres[np.random.default_rng(1).choice(len(centres), min(600, len(centres)), p=area / area.sum())]

    def tidy():
        bm = bmesh.new()
        bm.from_mesh(mesh)
        bmesh.ops.triangulate(bm, faces=bm.faces)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        bm.to_mesh(mesh)
        bm.free()
        mesh.update()

    goal = [TARGET]

    def collapse():
        tri = sum(len(p.vertices) - 2 for p in mesh.polygons)
        if tri > goal[0]:
            mod = obj.modifiers.new("reduce", "DECIMATE")
            mod.ratio = goal[0] / tri
            bpy.ops.object.modifier_apply(modifier=mod.name)
        return sum(len(p.vertices) - 2 for p in mesh.polygons)

    keep_mesh = mesh.copy()                                     # in case the first method fails

    # Method 1: rebuild the aircraft as one closed skin, then reduce. The originals are dozens
    # of separate, overlapping shells (fuselage, flaps, doors, fairings), which polygon
    # reduction tears apart. A single skin reduces cleanly and has a proper outside, so the
    # renderer can skip back faces. It only works when the original surfaces enclose a volume.
    voxel = size / 380.0

    def skin(thicken):
        """Rebuild the mesh as one skin. True if it follows nearly all of the original surface."""
        nonlocal mesh
        if thicken:
            # Open surfaces (a wing that is one sheet, a fuselage with the doors cut out) enclose
            # nothing, so the rebuild drops them. Giving every sheet a little thickness first
            # makes each one a volume of its own.
            fresh = keep_mesh.copy()
            obj.data = fresh
            bpy.data.meshes.remove(mesh)
            mesh = fresh
            wall = obj.modifiers.new("wall", "SOLIDIFY")
            wall.thickness = thicken
            wall.offset = 0.0
            bpy.ops.object.modifier_apply(modifier=wall.name)
        shell = obj.modifiers.new("shell", "REMESH")
        shell.mode = "VOXEL"
        shell.voxel_size = voxel
        shell.adaptivity = 0.0
        bpy.ops.object.modifier_apply(modifier=shell.name)
        if len(mesh.polygons) <= 50:
            return False
        collapse()
        tidy()
        surface = BVHTree.FromPolygons([v.co.copy() for v in mesh.vertices], [list(p.vertices) for p in mesh.polygons])
        near = 0
        for c in probe:
            hit = surface.find_nearest(Vector(c))
            if hit and hit[3] is not None and hit[3] < size * 0.012 + thicken / 2:
                near += 1
        return near / len(probe) >= 0.9

    solid = skin(0.0) or skin(voxel * 2.4)

    if not solid:
        # Method 2: reduce the original shells directly, snapping nearby points together when
        # edge collapse stalls. Rougher, and drawn two-sided because the outsides aren't reliable.
        obj.data = keep_mesh
        bpy.data.meshes.remove(mesh)
        mesh = keep_mesh
        goal[0] = TARGET_OPEN
        count = collapse()
        snap = size * 0.002
        while count > goal[0] * 1.2 and snap < size * 0.03:
            bm = bmesh.new()
            bm.from_mesh(mesh)
            bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=snap)
            bmesh.ops.dissolve_degenerate(bm, edges=bm.edges, dist=snap * 0.25)
            bm.to_mesh(mesh)
            bm.free()
            count = collapse()
            snap *= 1.6
        tidy()
    else:
        bpy.data.meshes.remove(keep_mesh)

    colour_of = []
    for poly in mesh.polygons:
        hit = paint.find_nearest(poly.center)
        colour_of.append(int(nearest[hit[2]]) if hit and hit[2] is not None else 0)

    pts = np.array([v.co[:] for v in mesh.vertices], np.float64)
    nose, right, up = axes(pts)
    p = np.stack([pts @ nose, pts @ right, pts @ up], axis=1)
    p[:, 0] -= (p[:, 0].min() + p[:, 0].max()) / 2
    p[:, 1] -= (p[:, 1].min() + p[:, 1].max()) / 2
    p[:, 2] -= np.median(p[:, 2])
    p *= span / np.ptp(p[:, 1])                             # the wingspan is the best-known dimension
    faces = [[*poly.vertices, colour_of[i]] for i, poly in enumerate(mesh.polygons) if len(poly.vertices) == 3]
    used = sorted({f[3] for f in faces})
    remap = {old: new for new, old in enumerate(used)}
    out = {
        "name": name, "kind": "heli" if kind == "H" else opts.get("kind", "jet"),
        "engines": opts.get("engines", 2), "span": span, "length": round(float(np.ptp(p[:, 0])), 1),
        "source": src.replace("\\", "/").split(".models-src/")[-1], "licence": licence, "credit": credit,
        "solid": bool(solid),
        "palette": [[int(round(c * 255)) for c in palette[i]] for i in used],
        "points": [[round(float(v), 2) for v in row] for row in p],
        "tris": [[f[0], f[1], f[2], remap[f[3]]] for f in faces],
    }
    path = os.path.join(out_dir, key + ".json")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, separators=(",", ":"))
    print("BAKED %-5s %-28s %6d -> %4d triangles, %2d colours, %s, length %.1f m (real %.1f), %d bytes"
          % (key, name[:28], before, len(faces), len(used), "closed skin" if solid else "open shells",
             out["length"], length, os.path.getsize(path)))


def main():
    args = sys.argv[sys.argv.index("--") + 1:]
    src_dir, out_dir, only = args[0], args[1], [a.upper() for a in args[2:]]
    os.makedirs(out_dir, exist_ok=True)
    for key, (rel, credit, licence) in SOURCES.items():
        if only and key not in only:
            continue
        try:
            bake(key, os.path.join(src_dir, rel), credit, licence, out_dir)
        except Exception as e:
            print("FAILED %s: %s: %s" % (key, type(e).__name__, e))


main()
