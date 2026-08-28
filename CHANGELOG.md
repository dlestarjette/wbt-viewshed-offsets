# Changelog

## v0.1.0 — 2026-08-28

First working version.

### Added

- `--offset_b`, a target height above ground (GRASS OFFSETB). The horizon sweep continues to run on bare-earth angles; only the visibility test uses the raised target, so raising the receiver does not also raise the terrain that blocks it.
- `--curvature` with `--refraction_k` and `--earth_radius`. Applied as a drop subtracted from elevations before angles are computed, so it corrects intervening terrain as well as the cell under test. `--refraction_k` has no default by design.
- `--max_dist`, masked to a disc rather than the bounding square. Terrain beyond the cap still occludes; the cap only decides what is reported.
- `--offset_a` as an alias for upstream's `--height`.
- `--z_factor`, for DEMs whose vertical and horizontal units differ.
- `--station_z`, an absolute observer ground elevation for observers not standing on the terrain — a boat, an aircraft, or a station over a DEM void.
- Geometry parameters are recorded in the output raster's metadata.

### Guards

- **Geographic DEMs are refused** for `--curvature` and `--max_dist`. Both are physical lengths measured in the DEM's own coordinates, so on a lat/long DEM they would be in degrees and silently meaningless. Detected by cell size rather than CRS metadata, which GeoTIFF readers populate unreliably. `--earth_radius` is the escape hatch for projected units that are not meters.
- **Stations on NoData cells are refused.** Upstream reads the DEM unconditionally, so such a station inherits `nodata + height` as its eye elevation; on a test DEM whose sea was NoData that placed an observer at −9997 m and produced a plausible raster with no error. This is the one deliberate divergence from upstream behavior: where upstream returns a meaningful result this tool is byte-identical to it, and where upstream returns garbage it declines.

### Fixed during development

- `--max_dist` initially left beyond-cap cells at the raster's initialization fill, which is NoData. They are now reported as `0` — the output is a count of stations, so zero is a true answer, and letting a parameter move the NoData mask would make capped and uncapped runs incomparable. Caught by `tests/behavior.py`.
- `install.py` now unlinks before copying. Overwriting a running binary in place corrupts it, and on Apple Silicon invalidates its code signature so it dies on next exec.

### Validation

Full results in [docs/validation.md](docs/validation.md).

- Parity with upstream at neutral settings: **0 differing cells of 419,592,012** per station, 7 stations, full 641.5 M-cell DEM. Run twice.
- Curvature against an independent DEM-pre-adjustment implementation: 9–236 differing cells of 100,000,000 per station.
- Closed-form geometry: horizons within 0.2–0.8% (grid discretization, one-sided), two-height sum rule within 0.0%. This is what fixes the magnitude of `--offset_b`.
- Monotonicity of `--offset_b` at full scale: **zero cells lost** across seven 50 km discs.
- Cross-engine comparison against a point-to-point LOS module surfaced that `offset_b = 0` is ill-conditioned — 69% of blocked sightlines to a ground-level target are blocked in the final 1% of the transect. See [docs/choosing-offsets.md](docs/choosing-offsets.md).

### Known limitations

- Tested on one DEM format, one projection, one terrain type.
- The facet-interpolated horizon is inherited from upstream, including its permissiveness at visibility boundaries. This tool adds parameters; it does not make the algorithm more accurate.
- `--station_z` applies to every station in a run, so mixed-platform observers need separate invocations.
