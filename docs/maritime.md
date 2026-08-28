# Over water

Marine and coastal visibility is where this tool's three additions stop being refinements and become the whole analysis. It is also where the two easiest ways to get a confidently wrong answer live. Both are avoidable; neither announces itself.

## 1. The sea must be real elevation, not NoData

**NoData does not occlude.** Cells with no elevation are treated as infinitely low, so a sightline passes straight through them. For voids in a terrestrial DEM that is the right default. For sea it is catastrophic, because the sea surface is the thing that creates a horizon.

Measured on a synthetic 60 km stretch of open water with a 60 m island at 40 km, eye height 1.7 m, curvature on:

| Sea encoded as | Island cells visible |
|---|---:|
| `0` m elevation | **0 / 400** — correctly beyond the horizon |
| NoData | 20 / 400 — visible, which is impossible |

Encode the sea as **0 m elevation**. If your DEM ships ocean as NoData — many do — fill it before running anything over water. Otherwise visibility across water is unbounded and every over-water result is wrong in the same direction, which is the hardest kind of error to notice.

A station sitting on a NoData cell is refused outright, with `--station_z` named as the fix. That guard exists because the failure it prevents is silent: on a test DEM whose sea was NoData, a station at sea inherited `nodata + height` as its eye elevation, placing the observer at −9997 m and producing a plausible raster with 300 visible cells and no error.

## 2. Curvature is not optional here

On land you can often ignore earth curvature below ~25 km, because terrain relief dominates the ignored drop. Over water there is no terrain, so **curvature is the only thing producing a horizon at all**. Without `--curvature`, an observer on a flat sea sees to the edge of the raster.

This inverts the usual priority. On land curvature is a correction; at sea it is the model.

## 3. Observers who are not on the ground

Use `--station_z` for the ground (or sea) elevation and `--offset_a` for the eye above it. `--station_z=0 --offset_a=1.7` places an eye 1.7 m above sea level regardless of what the DEM says beneath that point.

It applies to every station in the input, so mixed-platform runs need separate invocations.

## What vessel height actually buys

The tool reproduces the closed-form horizon to within grid resolution, and the two-height sum rule exactly ([validation.md, Gate 5](validation.md#gate-5--closed-form-geometry)), so these figures are the tool's own behavior rather than a textbook table. At k = 0.13:

| Observer | Eye height | Sees sea surface to | Sees a 5 m deck to | Sees a 500 m island to |
|---|---:|---:|---:|---:|
| Raft, canoe | 1.0 m | 3.8 km | 12.4 km | 89.4 km |
| Standing on a raft | 1.7 m | 5.0 km | 13.6 km | 90.6 km |
| Small boat deck | 3.0 m | 6.6 km | 15.2 km | 92.2 km |
| Ship deck | 5.0 m | 8.6 km | 17.1 km | 94.1 km |
| Crow's nest | 25 m | 19.1 km | 27.7 km | 104.7 km |

**The useful result is in the last two columns.** Going from a raft to a crow's nest — 25× the height — more than doubles the range at which you can spot another vessel (12.4 → 27.7 km) but extends island landfall by only 17% (89 → 105 km). Horizon distance goes as √h, and a 500 m island's own elevation swamps anything an observer can climb.

So for a seafaring study, vessel height is decisive for **vessel-to-vessel and vessel-to-shore-feature** questions and close to irrelevant for **island detection**, where what matters is the island's elevation — which comes from the DEM, not from either offset. Worth knowing before investing in rigging reconstruction.

## Refraction over water needs sweeping, not fixing

`--refraction_k` deliberately has no default, and over water that matters more than anywhere else.

The conventional 0.13 is a terrestrial average. Marine thermal gradients make the real coefficient genuinely variable, and temperature inversions over cool water produce looming — the effect by which sailors see islands that are geometrically below the horizon. Because the horizon scales as `sqrt(1/(1−k))`, that variation moves detection ranges by a lot.

Treat *k* as a sensitivity parameter over water. Run the range, report the range. A single-value marine visibility claim is understating its own uncertainty.

## Practical checklist

1. Fill ocean to **0 m**. Confirm it is not NoData.
2. Project to a CRS in meters. The tool refuses `--curvature` and `--max_dist` on geographic DEMs, since distances would be in degrees.
3. Always pass `--curvature` with an explicit `--refraction_k`.
4. Set `--station_z` if the observer is not on the terrain.
5. Set `--offset_b` to the height of what you are looking for — another vessel's deck, a beacon, a fire — not to 0.
6. Sweep *k*, and sweep the offsets if the conclusion is close.
