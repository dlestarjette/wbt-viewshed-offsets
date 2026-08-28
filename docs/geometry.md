# Geometry

What the tool computes, and why each addition is placed where it is. This matters more than usual because the algorithm is vendored rather than written from scratch: the design constraint throughout was to change as little as possible while making the new parameters correct.

## The inherited algorithm

Upstream's `Viewshed` computes, for every cell, the vertical angle from the observer to that cell. It then sweeps outward from the station along eight triangular facets, propagating a running maximum of those angles, interpolating between the two nearest already-processed cells. A cell is visible when its own angle meets or exceeds the maximum accumulated from cells nearer the observer.

This is a reference-plane style sweep. It is fast and it is an approximation: the interpolated horizon is not the exact horizon, and near visibility boundaries it is somewhat permissive compared with a converged point-to-point transect ([validation.md, Gate 2](validation.md#gate-2--cross-engine-agreement-and-what-it-revealed)). That behavior is inherited unchanged. This tool adds parameters to WhiteboxTools' algorithm; it does not make the algorithm more accurate.

## Target offset: separating two roles of one number

Upstream stores one grid of view angles and uses it for two different jobs:

1. the angle a cell contributes to the **occluding horizon**, and
2. the angle at which that cell is **tested for visibility**.

While the target sits on the ground these are the same number, so one grid serves both. A target offset breaks that, and the reason is worth stating: **raising the receiver must not also raise the terrain that blocks it.** If you simply added `offset_b` to every angle, the ridge in front of your target would grow by 1.7 m too, and the offset would cancel itself out.

So the two roles are separated. The horizon sweep continues to run on bare-earth angles, exactly as upstream computes them, and only the final visibility test uses the angle to `ground + offset_b`:

```
va_ground = (z_eff − stn_z) / dist            → feeds the horizon sweep
va_target = (z_eff + offset_b − stn_z) / dist  → used only for the final test
```

At `offset_b = 0` the two collapse into one value and the second grid is not even allocated. This is how GRASS `r.viewshed` treats its target offset; it is a correct treatment rather than an approximation.

## Curvature: which form, and why it is the only usable one

The target elevation is dropped before angles are computed:

```
    drop(d) = (1 − k) · d² / (2R)
```

Because the drop is applied to elevations rather than to the final test, it affects intervening terrain as well as the cell under test — which is what makes it occlusion-correct rather than cosmetic. Terrain 20 km away is lowered by its own drop, so it stops blocking things it should no longer block.

You may have seen curvature written the other way, as a **bulge added to intervening terrain** along a transect:

```
    bulge(d) = (1 − k) · d · (D − d) / (2R)
```

zero at both endpoints and maximal at the midpoint. **The two are the same geometry rearranged.** Substituting `z(d) → z(d) − c·d²` with `c = (1−k)/(2R)` on both the terrain and the target, then adding `c·d²` back to both sides of the blocking test, reproduces the bulge test exactly.

The drop form is the only one a viewshed can use, because the bulge form needs `D` — the observer-to-target distance — and in a viewshed every cell is a target with a different `D`. The bulge form works only for a fixed pair.

The practical consequence: **point-to-point line-of-sight code written the bulge way will agree with this tool.** That equivalence is not assumed; it is what Gate 2 tests, by comparing against an engine that uses the bulge form.

The correction is validated against closed-form geometry in [Gate 5](validation.md#gate-5--closed-form-geometry): on a flat sea it reproduces `sqrt(h/c)` to within grid resolution, and the two-height sum rule `sqrt(h₁/c) + sqrt(h₂/c)` exactly.

### Units

The drop is a physical length computed in the DEM's **horizontal** units and subtracted from an **elevation**. Upstream never has to care about unit mismatch, because it only ever compares angles and a consistent mismatch cancels. Curvature ends that cancellation, so two things become possible that cannot happen with the built-in tool:

- On a **geographic DEM** distances come out in degrees and the correction is meaningless. Detected by cell size and refused.
- On a DEM with **elevations in feet and coordinates in meters**, the drop is wrong by 3.28× unless you pass `--z_factor=0.3048`.

`--offset_a` and `--offset_b` are always in the DEM's vertical units and need no conversion.

## Distance cap

`--max_dist` decides what is **reported**, not what occludes. Terrain beyond the cap still blocks sightlines during the sweep, exactly as it would in an uncapped run; the cap is applied when the output is written. So a capped run equals an uncapped run masked to `d ≤ D` — verified exactly, not approximately.

It masks to a **disc**, not the bounding square, so "within D" means within D.

Cells beyond the cap are reported as **0**, not NoData. The output is a count of stations, so zero is a true answer — "no station sees this cell within its declared viewing distance" — whereas NoData would claim the cell could not be computed. Writing NoData would also make the NoData mask depend on a parameter, so capped and uncapped runs could no longer be compared cell for cell.

Truncating an unbounded viewshed at D and computing one on a D-radius window are equivalent, since occluding ground lies between observer and target. Using `--max_dist` rather than a pre-clipped DEM simply avoids writing that window to disk — which, on a large DEM with a many-point null distribution, is the dominant cost.

## What NoData means here

Cells with no elevation are treated as infinitely low: they never occlude, and no output value is written for them. That is the right default for voids in a terrestrial DEM.

It is the wrong default for sea, and that has real consequences — see [maritime.md](maritime.md).
