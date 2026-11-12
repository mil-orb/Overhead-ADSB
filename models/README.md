# Aircraft models

The 36 `.json` files here are low-polygon meshes for the plane view. They are **not
under the repo's MIT license.** Each one is converted from someone else's open-source
model and keeps that model's license.

| Files | Converted from | License |
|---|---|---|
| `A318 A319 A320 A321 A332 A333 A343 A346 A388 AT45 B737 B738 B739 B744 B748 B752 B753 B762 B763 B764 B772 B773 B789 BCS1 BCS3 C560 CRJ7 CRJ9 DH8D E170 E190 EC35 GLID P28A` | [Flightradar24/fr24-3d-models](https://github.com/Flightradar24/fr24-3d-models) at commit `dd53267` (2020-01-16), which is built from the [FlightGear](https://www.flightgear.org) project's aircraft in [FGMEMBERS](https://github.com/FGMEMBERS) | GPL-2.0, see [LICENSE-GPL-2.0.txt](LICENSE-GPL-2.0.txt) |
| `A359 B788` | [amvlab/aircraft-models](https://github.com/amvlab/aircraft-models) at commit `91d835e` (2026-08-08), the logo-free variants | CC BY 4.0, © 2026 amvlab, see [LICENSE-CC-BY-4.0.txt](LICENSE-CC-BY-4.0.txt) |

Each file also names its own source, license and credit in its `source`, `licence` and
`credit` fields, and the app shows the credit on the plane view.

## What was changed

Every model was converted by [tools/bake_models.py](../tools/bake_models.py):

- Landing gear details, cabin interiors, lights and aerials were left out.
- The surface was rebuilt as one closed skin, then reduced from 3,000 to 33,000 triangles
  down to about 1,000. Where the original was open sheets that enclose nothing (a wing
  that is one surface, a fuselage with the doors cut out), each sheet was first given
  about 25 cm of thickness so the rebuild had a volume to follow.
- Textures were dropped. Each triangle keeps the colour its texture had at that spot,
  from a palette of at most 24 colours per model.
- The model was turned to nose-forward, right-wing, up axes and scaled to the type's
  real wingspan in metres.

## Format

```json
{
  "name": "BOEING 737-800", "kind": "jet", "engines": 2, "span": 35.8, "length": 39.7,
  "source": "fr24/models/b738.glb", "licence": "GPL-2.0", "credit": "FLIGHTGEAR / FLIGHTRADAR24, GPL-2.0",
  "solid": true,
  "palette": [[r, g, b], ...],
  "points": [[x, y, z], ...],
  "tris": [[a, b, c, palette_index], ...]
}
```

x points out of the nose, y out of the right wing, z up, in metres. `solid` is true for a
closed skin, whose far-side triangles the renderer can skip.

## Coverage

These 36 files, plus relatives that borrow one (a 737 MAX 8 draws the 737-800, an E175
draws the E170), cover about 80 aircraft types. The app knows 213. The rest, including
every military type, most business jets, Cessnas and most helicopters, are drawn from
shapes the app generates from each type's real dimensions (`overhead/models.py`). Those
are original work under the repo's MIT license.

Flightradar24's 787-8 and A350 are left out because amvlab's are cleaner. Their set also
holds a few models under other terms (its README lists them); none of those are used here.
The Citation in the set has straight wings and a cross-shaped tail, so it is filed as the
Citation V (`C560`) and stands in for the other straight-wing Citations.

## Rebuilding

```bash
git clone --filter=blob:none --sparse https://github.com/Flightradar24/fr24-3d-models .models-src/fr24
```

```bash
git -C .models-src/fr24 checkout dd53267 && git -C .models-src/fr24 sparse-checkout set models
```

```bash
git clone https://github.com/amvlab/aircraft-models .models-src/amvlab && git -C .models-src/amvlab checkout 91d835e
```

```bash
blender -b --factory-startup --python tools/bake_models.py -- .models-src models
```

The two checkouts pin the exact commits these files were made from, so a rebuild gives
the same result. That is about 200 MB of downloads and needs Blender (tested with 5.2). Check the results
with `python tools/model_sheet.py out.png --small A320 B738 E170`.
