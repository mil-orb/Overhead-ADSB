# The flaps

How a split-flap module moves, what the code models, and why the drawing code is shaped the way it is. Everything here lives in [overhead/board.py](../overhead/board.py).

## What a real module does

A split-flap module (Solari is the name most people know) is a drum with 40 to 60 thin flaps hinged around it. Two flaps are visible at a time: the top half of a character on one, the bottom half on the next. A stepper motor turns the drum. Each step lets the top flap slip past a retaining finger and fall, which shows the next character.

Four things about that are easy to get wrong in an animation:

1. **It only goes forward.** To change a B into an A the drum goes through every flap from C onward and round again. A whole row changing looks like a wave because each module has a different distance to travel.
2. **The fall is not linear.** A released flap is a plate tipping over under gravity. It starts slowly, near upright, and is moving fastest as it lands.
3. **More than one flap is in the air.** At 12 flaps a second the drum releases a flap every 83 ms, and a fall takes about twice that. While a module is cycling there are usually two flaps falling at once, one further along than the other. This is most of what makes the motion look right.
4. **The light changes.** Light comes from above. As the flap tips forward its face turns away from the light and darkens. Past horizontal, its back (the bottom half of the next character) is facing up into the light and is briefly brighter than a flap at rest. While it overhangs the lower half of the window it also casts a shadow there.

## The fall

A thin plate of length L, hinged along one edge, with a little air drag:

```
theta'' = (3g / 2L) * sin(theta) - drag * theta' * |theta'|
```

`theta` is the angle from upright. `fall_curve` integrates this in steps of 0.1 ms and keeps a table of angle against time. The flap starts leaning 10° past upright, moving at the drum's own speed. With a 35 mm flap and 12 flaps a second:

| Time into the fall | Angle |
|---|---|
| 0 ms | 0° |
| 84 ms (half way) | 34° |
| 126 ms (three quarters) | 89° |
| 168 ms | 180°, landed |

So the flap spends the first half of its fall covering less than a fifth of the distance. An ease-in curve picked by eye gets close to this. A linear one looks like a slideshow.

## Drums

Each kind of position has its own drum, in a fixed order:

| Drum | Flaps | Used for |
|---|---|---|
| `ALPHA` | 41: blank, A to Z, 0 to 9, and `. - / °` | Flight, airports, type, direction |
| `NUM` | 12: blank, 0 to 9, `.` | Speed, altitude, distance, elevation |
| `CLOCK` | 11: 0 to 9, blank | The clock |
| `REMARKS` | 11 whole words | The status flap: `IN VIEW`, `MILITARY`, `CLOUD` and the rest |

A number drum gets from 3 to 4 in one flap. A letter drum can take 40. Every module in a row starts at the same moment and stops when it arrives. Each motor's speed is off by up to 6%, picked at random, so a row doesn't move in step.

## Drawing it on a Pi Zero

The first version drew each falling flap live: scale the face to its foreshortened height, darken it for its angle, draw the shadow, for every moving module, every frame. With all 36 modules moving that took 80 ms a frame on a Pi Zero W. Twelve frames a second, before the display got involved.

What it does now:

- The fall is sampled at 12 moments (`STEPS`).
- For each drum, for each pair of neighbouring flaps, for each of those moments, the upper half-window and the lower half-window are composed once: foreshortened flap, lighting, shadow, all baked in. That is `FaceSet`.
- The pictures are stored in the screen's own pixel format (16-bit on the Pi), with the brightness setting already applied.
- A frame of animation is then two plain copies per moving module. No scaling, no blending, no trigonometry.

That takes a full card flip to 21 ms a frame, 19 of it drawing and 1.5 copying to the display. Building the pictures takes time (the whole board is 4.7 seconds on a Pi Zero), so the board starts with the faces at rest and a background thread composes the rest, the most-used drums first. A flap that is asked for before its picture exists is composed on the spot.

The cost is memory and that start-up work: 12 pictures of each half-window for every pair of neighbouring flaps on every kind of drum.

## What reaches the screen

Drawing is no longer the limit. The display is. The screen hangs off SPI at 16 MHz, which carries about 2,080 screen lines a second (480 pixels, 16 bits each). The framebuffer driver sends the span of lines from the first changed one to the last.

- One digit changing touches 59 lines: 35 updates a second.
- A whole card changing touches 258 lines: 8 updates a second.

So the app counts lines. It measures how many it is sending and holds its frame rate to what the link can carry, so a slow link shows fewer, complete frames at the right speed and never builds a backlog. Animation runs on elapsed time, not frame count: a flap that gets 8 frames lands at the same moment as one that gets 30.

The SPI clock can be raised, which is the one change that makes a full-card flip visibly smoother. See [pi-setup.md](pi-setup.md#a-faster-screen). It depends on the panel and the wiring, which is why 16 MHz is the default.

## What it doesn't model

- **Sound.** The clatter is half the appeal of a real one.
- **Bounce.** A real flap rebounds a hair off the one below it.
- **Misalignment.** Real boards have flaps that sit slightly crooked and characters printed slightly off. These are all perfect.
- **Motion blur.** At 12 flaps a second a real flap is a blur for part of its fall. Here each frame is sharp.
