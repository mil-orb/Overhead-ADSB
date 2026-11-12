# How "in view" is worked out

Every aircraft within the query radius gets a score from 0 to 1 for "could a person standing here pick this out right now". The code is `visibility` in [overhead/sky.py](../overhead/sky.py), about 50 lines.

## Where the aircraft is

The feed gives latitude, longitude and altitude, updated every 5 seconds and often a second or two old. `Plane.at(t)` moves each aircraft forward along its track at its ground speed and vertical rate to the present moment, so the numbers on the card keep moving between updates.

`locate` turns that into three numbers as seen from your location:

- **range**: straight-line distance, in metres
- **azimuth**: compass bearing, degrees clockwise from north
- **elevation**: angle above the horizontal, in degrees

It uses a flat map around you with corrections for the earth's curvature and for the meridians converging, which is cheap enough for a Pi Zero to do for 60 aircraft every update. The tests hold it to the exact answer (`enu`) within 0.2% in range, 0.15° in bearing and 0.05° in elevation, out to 150 km.

## The five factors

Each factor is a number from 0 to 1. The score is all five multiplied together, so any one of them can hide an aircraft. The lowest one is recorded as the reason.

| Factor | What it measures | 0 at | 1 at |
|---|---|---|---|
| `LOW` | Elevation against your horizon in that direction | 1° below the horizon setting | 2° above it |
| `FAR` | By day, the wingspan's angular size. Above 8,000 m it is multiplied by 1.6, for the contrail. By night, how far lights carry. | 2.5 arcminutes (day), 75 km (night) | 6 arcminutes (day), 35 km (night) |
| `HAZE` | Length of the sight line inside the lowest 2 km of air, divided by the reported visibility | Path equal to the visibility | Path half the visibility |
| `CLOUD` | Chance of a gap through every cloud layer below the aircraft | Overcast below it | No cloud below it |
| `GLARE` | Angle between the aircraft and the sun | 6° or closer | 18° or more |

Between the two ends each factor follows a smoothstep curve, not a straight line.

Some details:

- **Horizon.** One setting (0, 5, 10 or 15 degrees) for trees and roofs. `horizon_mask` in `config.json` overrides it per compass direction, for a tall building on one side: `{"N": 20, "ESE": 12}`.
- **Angular size.** `wingspan / range` in arcminutes. A 36 m wingspan at 10 km is 12 arcminutes, about a third of the full moon. The wingspan comes from the type code when the app knows it, then from the ADS-B size category, then from a guess: 34 m for anything high or fast, 11 m otherwise.
- **Day or night.** The two `FAR` rules are blended by the sun's elevation, from all-night at 6° below the horizon to all-day at the horizon.
- **Range setting.** `FAR` also falls to 0 between the range setting and 4% beyond it, which is how the range setting takes effect.
- **Haze.** Haze sits in the lowest couple of kilometres. A sight line at 60° leaves that layer after 2.3 km. One at 3° stays in it for 38 km. That is why an aircraft overhead is visible on a day when the horizon is white.
- **Cloud.** Layers are taken at 500 m, 3,000 m and 8,000 m above you. Each one below the aircraft multiplies the chance by `1 - cover`. High cloud counts half, because cirrus is usually thin.
- **Glare.** Only applies while the sun is up.

## From score to the card

| Score | State | On the card |
|---|---|---|
| 0.55 or more | In view | Status flap `IN VIEW`, green lamp lit |
| 0.22 or more | Faint | Status flap `FAINT`, lamp off |
| below 0.22 | Hidden | Left off the card in the default mode. In other modes the status flap names the factor that hid it: `LOW`, `FAR`, `HAZE`, `CLOUD` or `GLARE`. |

An aircraft that is already `IN VIEW` stays there until its score drops below 0.40, and a `FAINT` one until it drops below 0.14. Without that, an aircraft sitting near a threshold would flap on and off the card.

Hidden aircraft that are closing on you are checked 45, 90, 150, 240 and 360 seconds ahead along their track. If one will score 0.55 or more at any of those times it is shown early with the time to go (`INBOUND`). At most 12 are checked per update, nearest first.

## Order on the card

1. In view
2. Faint
3. Military aircraft and emergency squawks (7500, 7600, 7700) that can't be seen. These are always kept.
4. Inbound
5. Everything else, which only the "everything" mode shows

Within each group, nearest first. An aircraft only moves ahead of the one in front when it is at least 8% closer, so two aircraft at nearly the same distance don't keep swapping. A military or emergency aircraft that has just appeared takes over the card once the current one has had 4 seconds, wherever it is in the order.

## What it gets wrong

- **Cloud is a grid cell, not your sky.** Open-Meteo reports cover for a cell several kilometres across, refreshed every 10 minutes. A single cloud over your house is invisible to it, and so is a hole in an overcast.
- **Contrails are assumed.** Any aircraft above 8,000 m gets the contrail bonus whether or not the air is making contrails that day.
- **No terrain.** Hills are whatever you put in the horizon setting.
- **Night is crude.** It is one distance for all aircraft. Landing lights pointed at you carry much further than 35 km, and a dim strobe on a small aircraft much less.
- **Wingspan is sometimes a guess.** A type the app doesn't know falls back to its size category, where a 757 and an A321 get the same span.
- **No account of contrast.** A white aircraft against white cloud and a dark one against blue sky score the same.
- **Aircraft without ADS-B don't exist.** Some military and older light aircraft don't broadcast a position. MLAT fills in some of them on adsb.lol, with positions that are a few seconds older.

If the card disagrees with what you see out of the window, the horizon setting is the first thing to change, then the range.
