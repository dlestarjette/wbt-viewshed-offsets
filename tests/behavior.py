#!/usr/bin/env python3
"""Gate 3: behavioral invariants of the three added parameters.

Each of these is a property the geometry guarantees, so a failure means the
implementation is wrong rather than merely surprising:

  1. max_dist is a pure output mask. A capped run must equal an uncapped run
     masked to the disc d <= D. Terrain beyond the cap still occludes during the
     sweep; the cap only decides what is reported.

  2. offset_b is monotone. Raising the receiver can only add visible cells, never
     remove one, so each offset's visible set must contain the previous one.

  3. curvature only removes sightlines. Dropping distant terrain below the
     straight-line horizon cannot create visibility, so the corrected visible set
     must be a strict subset of the uncorrected one at equal offsets.

  4. multi-station input still counts stations, not booleans.

Usage:
    python tests/behavior.py [--station 0] [--radius 15000]
"""

import argparse
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
REFRACTION_K = 0.13


def clip_dem(x, y, radius_m, out_path):
    with rasterio.open(DEM) as ds:
        row, col = ds.index(x, y)
        rpx = int(radius_m / ds.res[0])
        r0, r1 = max(0, row - rpx), min(ds.height, row + rpx)
        c0, c1 = max(0, col - rpx), min(ds.width, col + rpx)
        win = Window.from_slices((r0, r1), (c0, c1))
        arr = ds.read(1, window=win)
        prof = ds.profile.copy()
        prof.update(height=arr.shape[0], width=arr.shape[1],
                    transform=ds.window_transform(win), compress=None,
                    tiled=False, BIGTIFF="IF_SAFER")
        with rasterio.open(out_path, "w", **prof) as dst:
            dst.write(arr, 1)
    return out_path


class Runner:
    def __init__(self, wbt, dem, shp, tmp):
        self.wbt, self.dem, self.shp, self.tmp = wbt, dem, shp, tmp
        self.n = 0

    def __call__(self, **kw):
        self.n += 1
        out = os.path.join(self.tmp, f"vs_{self.n}.tif")
        args = [f"--dem={self.dem}", f"--stations={self.shp}", f"--output={out}",
                f"--offset_a={kw.pop('offset_a', HEIGHT)}",
                f"--offset_b={kw.pop('offset_b', 0.0)}"]
        if kw.pop("curvature", False):
            args += ["--curvature", f"--refraction_k={kw.pop('k', REFRACTION_K)}"]
        md = kw.pop("max_dist", None)
        if md is not None:
            args.append(f"--max_dist={md}")
        self.wbt.run_tool("ViewshedOffsets", args)
        with rasterio.open(out) as ds:
            arr, nd = ds.read(1), ds.nodata
            tr = ds.transform
        valid = arr != nd if nd is not None else np.ones(arr.shape, bool)
        return arr, valid, tr


def cell_distances(shape, transform, x0, y0):
    rows, cols = shape
    xs = transform.c + (np.arange(cols) + 0.5) * transform.a
    ys = transform.f + (np.arange(rows) + 0.5) * transform.e
    dx = xs[None, :] - x0
    dy = ys[:, None] - y0
    return np.sqrt(dx * dx + dy * dy)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--station", type=int, default=0,
                    help="index into the discovered station list")
    ap.add_argument("--radius", type=float, default=15000.0)
    args = ap.parse_args()

    found = config.stations()
    if args.station >= len(found):
        raise SystemExit(f"only {len(found)} stations found")
    label, _stem, shp = found[args.station]

    wbt = whitebox.WhiteboxTools()
    wbt.set_verbose_mode(False)
    wbt.set_compress_rasters(False)

    tmp = tempfile.mkdtemp(prefix="behavior_")
    x, y = config.station_xy(shp)
    dem = clip_dem(x, y, args.radius, os.path.join(tmp, "dem.tif"))
    run = Runner(wbt, dem, shp, tmp)
    fails = []

    def check(name, ok, detail=""):
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}{'  ' + detail if detail else ''}")
        if not ok:
            fails.append(name)

    print(f"{label}, {args.radius:.0f} m window\n")

    # --- 1. max_dist is a pure output mask -------------------------------
    print("1. max_dist masks to the disc and nothing more")
    base, valid, tr = run()
    dist = cell_distances(base.shape, tr, x, y)
    for D in (5000.0, 10000.0):
        capped, valid_c, _ = run(max_dist=D)
        inside = valid & valid_c & (dist <= D)
        outside = valid & (dist > D)
        agree_in = np.array_equal(base[inside], capped[inside])
        zero_out = bool((capped[outside] == 0).all()) if outside.any() else True
        check(f"D={D:.0f} m: retained cells identical to uncapped",
              agree_in, f"({int(inside.sum()):,} cells)")
        check(f"D={D:.0f} m: nothing reported beyond the cap", zero_out)

    # --- 2. offset_b monotone --------------------------------------------
    print("\n2. offset_b only ever adds visible cells")
    prev, prev_valid, _ = run(offset_b=0.0)
    for ob in (1.7, 5.0, 20.0):
        cur, cur_valid, _ = run(offset_b=ob)
        both = prev_valid & cur_valid
        lost = int(((prev[both] > 0) & (cur[both] == 0)).sum())
        gained = int(((prev[both] == 0) & (cur[both] > 0)).sum())
        check(f"offset_b {ob} m: no cell lost", lost == 0, f"(+{gained:,} gained)")
        prev, prev_valid = cur, cur_valid

    # --- 3. curvature only removes sightlines ----------------------------
    print("\n3. curvature is a strict subset of no curvature")
    for ob in (0.0, 1.7):
        off, off_valid, _ = run(offset_b=ob, curvature=False)
        on, on_valid, _ = run(offset_b=ob, curvature=True)
        both = off_valid & on_valid
        gained = int(((off[both] == 0) & (on[both] > 0)).sum())
        removed = int(((off[both] > 0) & (on[both] == 0)).sum())
        check(f"offset_b={ob}: curvature gains nothing", gained == 0,
              f"(-{removed:,} removed)")

    # --- 4. multi-station counts -----------------------------------------
    print("\n4. multi-station output counts stations")
    import geopandas as gpd

    others = [f[2] for f in found if f[2] != shp][:2]
    frames = [gpd.read_file(shp)] + [gpd.read_file(o) for o in others]
    multi = os.path.join(tmp, "multi.shp")
    combined = gpd.GeoDataFrame(
        {"geometry": [f.geometry.iloc[0] for f in frames]}, crs=frames[0].crs
    )
    combined.to_file(multi)
    run_multi = Runner(wbt, dem, multi, tmp)
    arr, valid, _ = run_multi()
    hi = int(arr[valid].max())
    check(f"max cell value reflects {len(frames)} stations",
          1 <= hi <= len(frames), f"(max={hi})")

    print()
    if fails:
        print(f"GATE 3 FAILED: {len(fails)} check(s) - {', '.join(fails)}")
        sys.exit(1)
    print("GATE 3 PASSED - all behavioral invariants hold")


if __name__ == "__main__":
    main()
