# Choosing offsets

`--offset_a` and `--offset_b` look like small conveniences. They are not: on gentle terrain the target offset alone can change a viewshed's area by more than half. This page is about picking them deliberately.

## What the two ends mean

- **`--offset_a`** — the observer's eye above the ground. Upstream calls this `--height` and this tool still accepts that name.
- **`--offset_b`** — the *target's* height above the ground: how tall the thing is that you are asking whether the observer can see.

The distinction is worth stating because it is easy to invert. `offset_b` does not move the observer. It changes the question from *"can I see the bare ground at that spot?"* to *"can I see something 1.7 m tall standing at that spot?"*

Everything in WhiteboxTools' built-in `Viewshed`, and in most viewshed tools that expose only one height, answers the first question. Often the second is the one you meant.

## Why `offset_b = 0` is a bad default

Not merely conservative — **ill-conditioned**. A target lying exactly on the ground surface is hidden or revealed by sub-cell terrain detail immediately in front of it, so its visibility is a coin-flip on information the DEM does not have.

Measured on real terrain ([validation.md, Gate 2](validation.md#gate-2--cross-engine-agreement-and-what-it-revealed)): with a ground-level target, **69% of blocked sightlines are blocked in the final 1% of the transect**, median block position 0.999 — at the target's own doorstep, not by intervening terrain. Raise the target 1.7 m and blocking falls by 72%, with 95% of what remains caused by genuine terrain in the first half of the path.

Different algorithms resolve that degeneracy differently and none of them is wrong. A grid sweep resolves it permissively, a dense transect restrictively. If your target has any real height, giving it that height removes the ambiguity instead of inheriting whichever way your software happens to break the tie.

## How much does it change?

Enough that it should never be left implicit, and by an amount that is **specific to your terrain and does not transfer** from anyone else's study.

The mechanism is slope, not distance. Raising a sightline by *h* over ground of slope *s* exposes a horizontal band of width *h/s* beyond each ridge, and those bands accumulate across every ridge in range. So:

- **Flat, open terrain** — plains, plateaus, steppe — narrow slopes, wide bands, **large** effect.
- **Steep terrain** — mountains, dissected uplands, volcanic islands — the band collapses, **small** effect.

For scale, on one gently dissected upland terrain, seven observers within a few tens of kilometers of each other, `offset_b` from 0 to 1.7 m:

| Observer (ranked by effect) | Viewshed area change |
|---|---:|
| 1 | +28.98% |
| 2 | +40.15% |
| 3 | +51.14% |
| 4 | +59.16% |
| 5 | +61.62% |
| 6 | +66.65% |
| 7 | +73.80% |

A 2.5× spread within a single landscape. Treat any published percentage — including these — as an illustration of magnitude, not a number to reuse.

The cells gained are overwhelmingly far-field: in that run the median gained cell was 10.8–23.2 km from the observer, because that is where terrain is closest to grazing incidence.

## Practical guidance

**Say what you are looking for, then set `offset_b` to its height.** A standing person is ~1.7 m by convention. A structure is its wall height. A fire or smoke column is its plume height. A rock-art panel partway up a cliff is its height on the face — which is a case where every tool lacking a target offset has been forcing an answer to the wrong question.

**Set `offset_a` to the observer's eye height, not to a round number**, and if the observer is not standing on the terrain at all — a boat, an aircraft, a platform — use `--station_z` for the ground elevation and `--offset_a` for the eye above it.

**Report both.** They are parameters of the question, not implementation details, and a viewshed area is meaningless without them. This tool writes both into the output raster's metadata, along with the curvature setting and any distance cap, so a file can be interrogated later rather than depending on someone's memory of the command line.

**Sweep them if the conclusion is close.** Because the effect is monotone — raising `offset_b` can only add visible cells, never remove one, verified across ~550 million cells — a sweep gives a clean bound rather than a scatter. If a result survives from `offset_b = 0` to your best estimate, the choice was not carrying the argument.

## A note for statistical comparisons

If you are comparing observed viewsheds against a null distribution of random points, changing `offset_b` moves **both**. Nodes get larger; so do the nulls. Whether a significance result survives depends on whether they move in the same proportion, which is not knowable in advance and is worth measuring before assuming either way.
