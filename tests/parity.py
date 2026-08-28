#!/usr/bin/env python3
"""Gate 1: the plugin must reproduce WhiteboxTools' built-in Viewshed exactly.

With --offset_b=0, no --curvature and no --max_dist, `ViewshedOffsets` takes the
same arithmetic path as upstream `Viewshed`. This test asserts that claim
against real terrain, cell for cell. Nothing else the plugin does is worth
trusting if this fails, because every result the tool would ever produce is a
modification of this baseline.

Two modes:

    --clip N      windowed comparison, N meters either side of each station.
                  Fast; use it while iterating.
    --full        the whole DEM, no window. Slow (~55 s per viewshed per engine)
                  and it is the gate that actually counts.

Usage:
    python tests/parity.py --clip 10000
    python tests/parity.py --full
"""

import argparse
import os
import sys
import tempfile
import time

import numpy as np
import rasterio
from rasterio.windows import Window
import whitebox

import config

DEM = config.DEM
HEIGHT = 1.7


def clip_dem(dem_path, x, y, radius_m, out_path):
    """Window the DEM around (x, y): a square of `radius_m` in pixel units, clamped at the edges."""
    with rasterio.open(dem_path) as ds:
        row, col = ds.index(x, y)
        rpx = int(radius_m / ds.res[0])
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


def read_arr(path):
    with rasterio.open(path) as ds:
        return ds.read(1), ds.nodata


def compare(path_a, path_b):
    """Return (n_diff, n_cells, detail) comparing two viewshed rasters."""
    a, nd_a = read_arr(path_a)
    b, nd_b = read_arr(path_b)
    if a.shape != b.shape:
        return -1, 0, f"shape mismatch {a.shape} vs {b.shape}"

    # Treat each raster's own nodata as a single equivalence class, so a
    # difference in how nodata is encoded is not reported as a visibility
    # difference. Any real disagreement in the 0/1 body still shows up.
    ma = a == nd_a if nd_a is not None else np.zeros(a.shape, bool)
    mb = b == nd_b if nd_b is not None else np.zeros(b.shape, bool)
    if not np.array_equal(ma, mb):
        return int((ma != mb).sum()), a.size, "nodata masks differ"

    valid = ~ma
    diff = (a[valid] != b[valid]).sum()
    return int(diff), int(valid.sum()), ""


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--clip", type=float, help="window radius in meters")
    g.add_argument("--full", action="store_true", help="use the whole DEM")
    ap.add_argument("--height", type=float, default=HEIGHT)
    args = ap.parse_args()

    wbt = whitebox.WhiteboxTools()
    wbt.set_verbose_mode(False)
    wbt.set_compress_rasters(False)

    tmp = tempfile.mkdtemp(prefix="parity_")
    failures = []
    print(f"{'station':<10} {'cells':>14} {'differing':>10}  result")
    print("-" * 52)

    for sid, stem, shp in config.stations():
        if args.full:
            dem = DEM
        else:
            x, y = config.station_xy(shp)
            dem = clip_dem(DEM, x, y, args.clip, os.path.join(tmp, f"dem_{stem}.tif"))

        out_up = os.path.join(tmp, f"up_{stem}.tif")
        out_pl = os.path.join(tmp, f"pl_{stem}.tif")

        t0 = time.time()
        wbt.viewshed(dem=dem, stations=shp, output=out_up, height=args.height)
        t_up = time.time() - t0

        t0 = time.time()
        wbt.run_tool(
            "ViewshedOffsets",
            [
                f"--dem={dem}",
                f"--stations={shp}",
                f"--output={out_pl}",
                f"--offset_a={args.height}",
                "--offset_b=0",
            ],
        )
        t_pl = time.time() - t0

        if not os.path.exists(out_pl):
            print(f"{sid:<10} {'-':>14} {'-':>10}  FAIL (plugin wrote no output)")
            failures.append(sid)
            continue

        n_diff, n_cells, detail = compare(out_up, out_pl)
        ok = n_diff == 0
        note = "PASS" if ok else f"FAIL {detail}"
        print(f"{sid:<10} {n_cells:>14,} {n_diff:>10,}  {note}  [{t_up:.1f}s / {t_pl:.1f}s]")
        if not ok:
            failures.append(sid)

        for p in (out_up, out_pl):
            if os.path.exists(p):
                os.remove(p)
        if not args.full and os.path.exists(dem):
            os.remove(dem)

    print("-" * 52)
    if failures:
        print(f"GATE 1 FAILED for: {', '.join(failures)}")
        sys.exit(1)
    print("GATE 1 PASSED - plugin is cell-identical to upstream Viewshed")


if __name__ == "__main__":
    main()
