#!/usr/bin/env python3
"""Gate 1b: the plugin's native curvature vs. an independent DEM pre-adjustment.

The reference viewsheds (WBTVO_REF_DIR) must come from a completely independent
method: a pipeline that subtracts a per-cell curvature drop from a clipped DEM
in float32, writes that to a temporary GeoTIFF, and hands it to the unmodified
WhiteboxTools viewshed. The plugin instead applies the same drop in f64 inside
the visibility loop. Two independent implementations of one geometry agreeing
is a far stronger check than either alone, and where such reference rasters
already exist it costs nothing to run.

Exact equality is NOT expected, for two reasons that are understood in advance:

  1. float32 quantisation. The pre-adjustment stores `z - drop` as float32, so
     elevations carry ~1e-4 m of rounding; the plugin keeps f64 throughout.
     Cells sitting within that margin of the horizon can flip either way.

  2. Distance origin. The pre-adjustment measures d from the observer's PIXEL
     CENTRE (`(row - lr) * res`), because it is building a raster. The plugin
     measures d from the observer's true coordinate, because that is what
     upstream's own view-angle computation uses (`x - stn_x`). At 10 m
     resolution this is a sub-pixel offset, worth ~0.05 m of drop at 50 km.

So this test reports the disagreement and characterises it rather than demanding
zero. A few thousand flipped cells out of ~10^8, scattered along visibility
boundaries, is the expected and acceptable result. A large or spatially
structured disagreement is not, and means the curvature term is wrong.

Usage:
    python tests/curvature_crosscheck.py
"""

import os
import sys
import tempfile

import numpy as np
import rasterio
from rasterio.windows import Window
import whitebox

import config

DEM = config.DEM

HEIGHT = 1.7
CLIP_RADIUS_M = 50000.0
REFRACTION_K = 0.13
EARTH_RADIUS_M = 6371000.0

# Flag anything above this fraction of valid cells as a real disagreement rather
# than float32 / sub-pixel noise.
TOLERANCE_FRAC = 5e-4


def clip_dem(x, y, out_path):
    """Window the DEM around (x, y) exactly as the reference pipeline did."""
    with rasterio.open(DEM) as ds:
        row, col = ds.index(x, y)
        rpx = int(CLIP_RADIUS_M / ds.res[0])
        r0, r1 = max(0, row - rpx), min(ds.height, row + rpx)
        c0, c1 = max(0, col - rpx), min(ds.width, col + rpx)
        win = Window.from_slices((r0, r1), (c0, c1))
        arr = ds.read(1, window=win)
        prof = ds.profile.copy()
        prof.update(
            height=arr.shape[0],
            width=arr.shape[1],
            transform=ds.window_transform(win),
            compress=None,
            tiled=False,
            BIGTIFF="IF_SAFER",
        )
        with rasterio.open(out_path, "w", **prof) as dst:
            dst.write(arr, 1)
    return out_path


def main():
    wbt = whitebox.WhiteboxTools()
    wbt.set_verbose_mode(False)
    wbt.set_compress_rasters(False)

    tmp = tempfile.mkdtemp(prefix="curvcheck_")
    print(f"reference: {config.REF_DIR}/{config.REF_PATTERN}")
    print(f"plugin:    --curvature --refraction_k={REFRACTION_K} --offset_b=0 "
          f"--offset_a={HEIGHT}, same 50 km window\n")
    print(f"{'station':<10} {'valid cells':>13} {'differing':>10} {'frac':>10}  result")
    print("-" * 58)

    flagged, missing = [], []
    for sid, stem, shp in config.stations():
        ref, _ = config.reference_paths(stem)
        if not os.path.exists(ref) or not os.path.exists(shp):
            print(f"{sid:<10} {'-':>13} {'-':>10} {'-':>10}  SKIP (missing input)")
            missing.append(sid)
            continue

        x, y = config.station_xy(shp)
        dem_clip = clip_dem(x, y, os.path.join(tmp, f"dem_{stem}.tif"))
        out = os.path.join(tmp, f"pl_{stem}.tif")

        wbt.run_tool(
            "ViewshedOffsets",
            [
                f"--dem={dem_clip}",
                f"--stations={shp}",
                f"--output={out}",
                f"--offset_a={HEIGHT}",
                "--offset_b=0",
                "--curvature",
                f"--refraction_k={REFRACTION_K}",
                f"--earth_radius={EARTH_RADIUS_M}",
            ],
        )

        if not os.path.exists(out):
            print(f"{sid:<10} {'-':>13} {'-':>10} {'-':>10}  FAIL (no output)")
            flagged.append(sid)
            continue

        with rasterio.open(ref) as a, rasterio.open(out) as b:
            arr_a, nd_a = a.read(1), a.nodata
            arr_b, nd_b = b.read(1), b.nodata
            if arr_a.shape != arr_b.shape:
                print(f"{sid:<10} {'-':>13} {'-':>10} {'-':>10}  "
                      f"FAIL window mismatch {arr_a.shape} vs {arr_b.shape}")
                flagged.append(sid)
                continue

        valid = np.ones(arr_a.shape, bool)
        if nd_a is not None:
            valid &= arr_a != nd_a
        if nd_b is not None:
            valid &= arr_b != nd_b

        n_valid = int(valid.sum())
        n_diff = int((arr_a[valid] != arr_b[valid]).sum())
        frac = n_diff / n_valid if n_valid else 0.0
        ok = frac <= TOLERANCE_FRAC
        print(f"{sid:<10} {n_valid:>13,} {n_diff:>10,} {frac:>10.2e}  "
              f"{'ok' if ok else 'FLAGGED'}")
        if not ok:
            flagged.append(sid)

        for p in (dem_clip, out):
            if os.path.exists(p):
                os.remove(p)

    print("-" * 58)
    if missing:
        print(f"skipped (inputs absent): {', '.join(missing)}")
    if flagged:
        print(f"GATE 1b FLAGGED for: {', '.join(flagged)}")
        print("Disagreement exceeds the float32 / sub-pixel noise budget. "
              "Inspect before trusting the curvature term.")
        sys.exit(1)
    print(f"GATE 1b PASSED - agreement within {TOLERANCE_FRAC:.0e} of valid cells "
          "on every station")


if __name__ == "__main__":
    main()
