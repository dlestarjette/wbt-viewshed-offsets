# Validation

A viewshed tool is easy to write and hard to trust. Anything here could produce a plausible raster while being wrong, and nobody looking at the map would know. So this document records what was actually measured, with the numbers, rather than asserting that the tool works.

Five gates, in rough order of how much they license. All were run on 2026-08-15 against `v0.1.0`.

The first four use a real 641.5 million cell 10 m DEM in a projected metric CRS, with seven viewing stations. The stations are reported under neutral labels; the tests discover whatever stations they are pointed at and label them the same way, so restricted station locations never appear in output. **Gate 5 needs no external data** and can be run by anyone.

---

## Gate 1 — Identical to upstream at neutral settings

**Claim.** With `--offset_b=0`, no `--curvature` and no `--max_dist`, this tool computes exactly what WhiteboxTools' built-in `Viewshed` computes.

This is the gate everything else rests on. If the baseline drifts, every result the tool produces is a difference from an unknown quantity.

It is partly a structural argument rather than an empirical one: the added parameters are inert at their defaults by construction. `curv_coeff` is exactly `0.0`, so the branch binding `z_eff` returns `z` unchanged — the same operation upstream performs, not merely a value equal to it. `beyond_cap` is always false when `max_dist` is infinite. The target-angle grid is not allocated when `offset_b == 0`, and the visibility test falls back to the upstream expression verbatim.

**Measured**, full DEM, no windowing, observer height 1.7 m:

| Station | Cells compared | Differing |
|---|---:|---:|
| Station A | 419,592,012 | **0** |
| Station B | 419,592,012 | **0** |
| Station C | 419,592,012 | **0** |
| Station D | 419,592,012 | **0** |
| Station E | 419,592,012 | **0** |
| Station F | 419,592,012 | **0** |
| Station G | 419,592,012 | **0** |

Run twice — once during development and again against the shipped binary. Per-viewshed time was 47–57 s for both engines, so the added parameters cost nothing when unused.

`tests/parity.py --full`

### The one deliberate divergence

A viewing station sitting on a NoData cell is **refused**. Upstream reads the DEM unconditionally and returns `nodata + height` as the eye elevation; on a test DEM whose sea is NoData, that placed an observer at −9997 m and wrote a plausible raster with 300 visible cells, no error and no warning. Refusing is not a different answer to the same question — it is declining an ill-posed one. See [maritime.md](maritime.md).

---

## Gate 1b — Curvature against an independent implementation

**Claim.** The in-loop curvature correction matches a completely separate implementation of the same geometry.

The reference is a pipeline that pre-adjusts a clipped DEM in float32, writes it to a temporary GeoTIFF, and hands that to the *unmodified* WhiteboxTools viewshed. Different arithmetic precision, different data path, different software doing the visibility computation. Agreement between the two is much stronger evidence than either could give alone.

**Measured**, 50 km windows, k = 0.13, observer 1.7 m, target 0 m:

| Station | Valid cells | Differing | Fraction |
|---|---:|---:|---:|
| Station A | 100,000,000 | 21 | 2.1e-7 |
| Station B | 100,000,000 | 9 | 9.0e-8 |
| Station C | 100,000,000 | 236 | 2.4e-6 |
| Station D | 100,000,000 | 18 | 1.8e-7 |
| Station E | 100,000,000 | 37 | 3.7e-7 |
| Station F | 100,000,000 | 51 | 5.1e-7 |
| Station G | 100,000,000 | 29 | 2.9e-7 |

Exact agreement was not expected and is not required. Two known differences account for the residual: the reference stores adjusted elevations as float32 (~1e-4 m of rounding) while this tool keeps f64 throughout; and the reference measures distance from the observer's *pixel center* because it is building a raster, while this tool measures from the observer's true coordinate, matching what the view-angle computation itself uses. Both push a handful of boundary cells either way.

`tests/curvature_crosscheck.py`

---

## Gate 2 — Cross-engine agreement, and what it revealed

**Claim.** The target offset agrees with an independent point-to-point line-of-sight engine.

The witness is a transect-based LOS module that predates this plugin and has always honored a target offset. It is independent in every respect that matters: it interpolates 500 elevation samples along a straight line rather than sweeping a grid, and it expresses curvature as a *bulge added to intervening terrain*, `(1−k)·d·(D−d)/(2R)`, rather than a *drop subtracted from the target*, `(1−k)·d²/(2R)`. So this also tests the claim in [geometry.md](geometry.md) that those two forms are the same geometry rearranged.

**Measured**, 3,149 observer–target pairs across 7 observers, stratified to over-sample boundary cells:

| Target offset | Engines agree | Plugin-only visible | LOS-only visible |
|---|---:|---:|---:|
| 0.0 m | 93.74% | 98 | 99 |
| 1.7 m | 95.49% | 142 | 0 |

The one-directional disagreement at 1.7 m looked like a defect. It is not; it is a property of `offset_b = 0`, and finding it was the most useful thing this gate did.

Sweeping the transect sampling density showed the reference engine finding steadily more occluders as it converged:

| Samples per transect | Spacing at 25 km | Agrees the cell is visible |
|---:|---:|---:|
| 100 | 250 m | 98.10% |
| 500 | 50 m | 92.10% |
| 2,000 | 12.5 m | 87.52% |
| 5,000 | 5 m | 79.62% |
| 10,000 | 2.5 m | 79.43% |

Converged below the 10 m DEM resolution, as expected. Locating *where* along each transect the blocking occurred settled what was happening:

| Target offset | Pairs blocked | Median block position | Blocks in the final 1% |
|---|---:|---:|---:|
| 0.0 m | 472 / 1049 | **0.999** | **69.1%** |
| 1.7 m | 132 / 1049 | 0.002 | 0.8% |

At ground level, most blocked sightlines are blocked at the target's own doorstep — by the terrain immediately in front of it. Raising the target 1.7 m cuts blocking by 72% and moves 95% of what remains into genuine intervening terrain in the first half of the transect.

**Conclusion: `offset_b = 0` is ill-conditioned.** Whether a point lying exactly on the ground surface is visible turns on sub-cell terrain detail at that point. A facet sweep resolves that permissively; a converged transect resolves it restrictively. Neither is wrong, and the disagreement is not evidence against either. It is an argument for setting a non-zero target offset on better grounds than convention — see [choosing-offsets.md](choosing-offsets.md).

`tests/cross_engine.py`, `tests/area_crosscheck.py`

---

## Gate 3 — Behavioral invariants

Properties the geometry guarantees, so a failure means the implementation is wrong rather than merely surprising.

| Invariant | Result |
|---|---|
| `--max_dist` retains uncapped values inside the disc | exact at D = 5 km (785,380 cells) and D = 10 km (3,141,573 cells) |
| `--max_dist` reports 0 beyond the cap | holds |
| `--offset_b` never removes a visible cell | holds at 1.7 m (+531,514), 5 m (+553,122), 20 m (+1,492,355), **0 lost in every case** |
| curvature-on is a strict subset of curvature-off | holds at both offsets (−110,067 and −142,161 cells removed) |
| multi-station output counts stations | max cell value 3 for 3 stations |

Monotonicity was then confirmed at full scale rather than by sample: across all seven 50 km discs — roughly 550 million cells — **zero cells are lost** when the target is raised from 0 to 1.7 m.

`tests/behavior.py`

---

## Gate 4 — Installs and is discovered

`install.py` places the binary and its JSON descriptor into an existing WhiteboxTools installation. `wbt.list_tools()` then returns `['viewshed', 'viewshed_offsets']`, and the tool runs through `wbt.run_tool("ViewshedOffsets", …)`.

WhiteboxTools discovers plugins at runtime from those descriptors and spawns them as subprocesses. There is no registration step and no license gate on third-party plugins.

---

## Gate 5 — Closed-form geometry

**Claim.** The curvature term and both offsets match analytic geometry.

Every gate above compares this tool to other software. This one compares it to arithmetic, and is the only gate that depends on nothing else being right. It builds its own synthetic DEM, so anyone can run it.

On a perfectly flat sea with the drop written as `c·d²`, `c = (1−k)/2R`, an eye at height *h* sees the surface to exactly `sqrt(h/c)`, and two objects of heights *h₁* and *h₂* are mutually visible to `sqrt(h₁/c) + sqrt(h₂/c)`.

**Measured**, 80 km extent, 50 m cells, k = 0.13:

| Eye height | Measured horizon | Analytic | Error |
|---:|---:|---:|---:|
| 1.0 m | 3.86 km | 3.83 km | +0.8% |
| 1.7 m | 5.02 km | 4.99 km | +0.7% |
| 3.0 m | 6.66 km | 6.63 km | +0.5% |
| 5.0 m | 8.59 km | 8.56 km | +0.4% |
| 10.0 m | 12.14 km | 12.10 km | +0.3% |
| 25.0 m | 19.17 km | 19.14 km | +0.2% |

Error is one-sided and shrinks as the horizon grows relative to the cell size, which is what grid discretization looks like — the last visible cell sits at or just past the true horizon.

The sum rule, observer fixed at 1.7 m. **This is the only measurement that fixes the magnitude of `--offset_b` against an external truth** rather than checking that it moves in the right direction:

| Target height | Measured | Analytic | Error |
|---:|---:|---:|---:|
| 1.7 m | 9.98 km | 9.98 km | −0.0% |
| 5.0 m | 13.55 km | 13.55 km | −0.0% |
| 15.0 m | 19.81 km | 19.81 km | −0.0% |
| 25.0 m | 24.12 km | 24.12 km | +0.0% |

`tests/horizon_analytic.py`

---

## What is not validated

Stated plainly, because a tested surface this narrow is worth naming.

- **One DEM format, one projection.** 10 m float32 GeoTIFF in a projected UTM CRS, single band. Other formats go through the same `whitebox_raster` crate and ought to work; "ought to" is the honest word.
- **The horizon approximation is inherited, not fixed.** Gate 2 shows the facet-interpolated sweep is permissive at visibility boundaries relative to a converged transect. You get WhiteboxTools' algorithm *plus* offsets, not a more accurate algorithm. For boundary-exact work, GRASS `r.viewshed`'s exact method is a different tool.
- **No LiDAR or sub-meter testing.** Nothing suggests a problem; nothing has been run.
- **Magnitudes do not transfer.** Any figure here for how much `--offset_b` changes a viewshed is specific to this terrain. See [choosing-offsets.md](choosing-offsets.md).
