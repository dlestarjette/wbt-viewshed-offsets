# wbt-viewshed-offsets

A [WhiteboxTools](https://github.com/jblindsay/whitebox-tools) plugin that gives `Viewshed` three things it does not have: a **target offset** (GRASS-style OFFSETB), **earth-curvature and refraction correction**, and a **maximum viewing distance**.

```
viewshed_offsets run --dem=dem.tif --stations=sites.shp -o=vs.tif \
    --offset_a=1.7 --offset_b=1.7 \
    --curvature --refraction_k=0.13 \
    --max_dist=50000
```

## Why

WhiteboxTools' `Viewshed` takes exactly four parameters — `--dem`, `--stations`, `--output`, `--height`. The height applies to the **observer only**, so every target is a receiver lying flat on the ground; there is no curvature correction at any distance; and there is no distance cap.

For short-range work none of that matters much. For regional visibility analysis all three do, and the usual workarounds — pre-adjusting the DEM to fake curvature, pre-clipping it to fake a cap — cost a full raster write per observer, which is exactly the cost that makes large null distributions painful. This plugin does all three inside the visibility loop, so no intermediate rasters are written.

The target offset is the one people most often want without knowing the flag is missing. `--offset_b` changes the question from *"can I see the bare ground there?"* to *"can I see something 1.7 m tall standing there?"* On gentle terrain that can change a viewshed's area by more than half.

## What it is, and what it is not

`src/main.rs` began as a **verbatim copy** of upstream's `viewshed.rs` and keeps its visibility algorithm — the eight-facet view-angle sweep. The first commit in this repository is that file, unmodified, so every change to the algorithm is visible as a diff against it. Each modification carries an inline marker naming what changed, why, and whether it can move a result, with the original lines retained beside it, commented out.

The governing invariant:

> With `--offset_b=0`, no `--curvature` and no `--max_dist`, output is identical to upstream `Viewshed`, cell for cell.

That is not a design goal, it is a test. It runs on a 641.5-million-cell DEM and requires **zero** differing cells; it currently passes on 419,592,012 cells per station across seven stations. See [docs/validation.md](docs/validation.md).

This adds parameters to WhiteboxTools' algorithm. It does not make the algorithm more accurate — the facet-interpolated horizon is inherited as-is, permissiveness at visibility boundaries included.

## Parameters

| Flag | Default | Meaning |
|---|---|---|
| `-d`, `--dem` | required | Input DEM raster. |
| `--stations` | required | Input viewing-station vector (points). |
| `-o`, `--output` | required | Output raster: count of stations visible from each cell. |
| `--offset_a`, `--height` | `2.0` | Observer height above ground, in z units. `--height` is upstream's name, still accepted. |
| `--offset_b` | `0.0` | **Target** height above ground. `0` reproduces upstream exactly. |
| `--max_dist` | unlimited | Maximum viewing distance, masked to a **disc**. |
| `--curvature` | off | Apply curvature + refraction correction. Requires `--refraction_k`. |
| `--refraction_k` | **none** | Refraction coefficient. Deliberately has no default. |
| `--earth_radius` | `6371000` | Earth radius, in the DEM's **horizontal** units. |
| `--z_factor` | `1.0` | Converts the DEM's **vertical** units to its horizontal units. `--curvature` only. |
| `--station_z` | from DEM | Absolute ground elevation for stations, replacing the DEM lookup. |

All of them, plus the curvature setting, are written into the output raster's metadata — so a file can be interrogated later instead of depending on someone's memory of the command line.

**`--refraction_k` has no default on purpose.** A correction whose strength nobody stated is how silent inconsistency starts. If you ask for `--curvature` you have to say how strong. `0.13` is the common geodetic value; the tool will not pick it for you.

## Read before you rely on it

Three things bite people, all documented with measurements:

- **[Choosing offsets](docs/choosing-offsets.md)** — why `offset_b = 0` is not a safe default but an *ill-conditioned* one, and why the size of the effect is specific to your terrain and does not transfer from anyone else's published figure.
- **[Over water](docs/maritime.md)** — sea encoded as NoData removes the horizon entirely and gives unbounded visibility across water. It must be real 0 m elevation. Also: what vessel height actually buys, and why refraction needs sweeping rather than fixing.
- **[Units](docs/geometry.md#units)** — `--curvature` and `--max_dist` are physical lengths, so unlike upstream they are not unit-agnostic. Geographic DEMs are refused; feet-over-meters DEMs need `--z_factor`.

## Install

Needs a Rust toolchain and an existing WhiteboxTools 2.x install (the `whitebox` Python package will do).

```
cargo build --release
python install.py                                  # into the active interpreter's whitebox
python install.py --python /path/to/venv/bin/python
```

`install.py` copies the binary and `viewshed_offsets.json` into WhiteboxTools' `plugins/` directory. WBT discovers plugins at runtime from those descriptors and runs them as subprocesses — there is no registration step, and no license gate on third-party plugins.

```python
import whitebox
w = whitebox.WhiteboxTools()
print([t for t in w.list_tools() if "viewshed" in t])   # -> ['viewshed', 'viewshed_offsets']
w.run_tool("ViewshedOffsets", ["--dem=dem.tif", "--stations=s.shp",
                               "--output=vs.tif", "--offset_b=1.7"])
```

## Tests

| Test | Asserts | Needs data? |
|---|---|---|
| `tests/horizon_analytic.py` | Curvature and both offsets match closed-form horizon geometry. Depends on no other implementation. | no — builds its own |
| `tests/parity.py --full` | Cell-identical to upstream at neutral settings. | yes |
| `tests/curvature_crosscheck.py` | Curvature agrees with an independent DEM-pre-adjustment implementation. | yes |
| `tests/cross_engine.py` | Offsets agree with an independent point-to-point LOS engine. | yes |
| `tests/behavior.py` | `max_dist` is a pure mask; `offset_b` is monotone; curvature only removes sightlines; multi-station counting. | yes |

`horizon_analytic.py` runs anywhere. The rest need real terrain, which is not in the repository — point them at your own via environment variables:

```
export WBTVO_DEM=/path/to/dem.tif
export WBTVO_STATIONS=/path/to/station/shapefiles     # a directory
export WBTVO_REF_DIR=/path/to/reference/viewsheds     # cross-check tests only
export WBTVO_REF_PATTERN='viewshed_{id}.tif'
export WBTVO_OFFB_PATTERN='viewshed_{id}_b170.tif'
export WBTVO_LOS_MODULE=/path/to/los_witness.py     # cross_engine / area_crosscheck only
```

`cross_engine.py` and `area_crosscheck.py` also need an independent point-to-point line-of-sight implementation as a witness; `tests/config.py` documents the two functions it must expose.

Stations are discovered by globbing that directory and reported under neutral labels (Station A, B, …) in sorted filename order. Station identifiers are never printed, stored, or written to any output, so stations whose locations are restricted (protected sites, private infrastructure, sensitive habitat) can be used as test data without leaking into logs or this repository. See `tests/config.py`.

Results, with numbers: [docs/validation.md](docs/validation.md).

## Documentation

- [Geometry](docs/geometry.md) — what it computes and why each addition sits where it does
- [Validation](docs/validation.md) — every gate, measured, including what is *not* tested
- [Choosing offsets](docs/choosing-offsets.md) — practical guidance
- [Over water](docs/maritime.md) — marine and coastal work
- [Changelog](CHANGELOG.md)

## Status

Early. Validated on one DEM, one projection, one terrain type. The neutral-path parity guarantee is structural and should hold anywhere; everything else has been tested on a narrower surface than its users will apply it to. Issues and terrain counter-examples welcome.

## License

MIT. Derived from WhiteboxTools (MIT, © 2017–2021 John Lindsay) — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
