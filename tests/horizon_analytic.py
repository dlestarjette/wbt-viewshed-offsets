#!/usr/bin/env python3
"""Gate 5: the curvature and offset terms against closed-form geometry.

Every other test in this directory compares the plugin to another piece of
software -- upstream WhiteboxTools, a DEM pre-adjustment pipeline, a
point-to-point LOS module. Those are good checks, but they all rest on the other
implementation being right.

This one does not. On a perfectly flat sea, the geometry has an exact answer.
With the curvature drop written as

    drop(d) = c * d^2,    c = (1 - k) / (2R)

an eye at height h sees the sea surface out to exactly

    d = sqrt(h / c)

which is the standard horizon distance sqrt(2Rh/(1-k)); and two objects of
heights h1 and h2 are mutually visible out to

    d = sqrt(h1 / c) + sqrt(h2 / c)

the classic sum-of-horizons result. The second identity is what actually pins
down `--offset_b`: it is the only test here that fixes the offset's magnitude
against an external truth rather than merely checking that it moves the right
direction.

The test builds its own synthetic DEM, so it needs no project data and can be
run by anyone. Residual error is grid discretization -- the last visible cell
sits at or just past the true horizon -- and shrinks as the horizon grows
relative to the cell size.

Usage:
    python tests/horizon_analytic.py [--res 50] [--extent-km 80]
"""

import argparse
import os
import shutil
import sys
import tempfile

import numpy as np
import rasterio
import geopandas as gpd
import shapely.geometry as sg
from rasterio.transform import from_origin
import whitebox

EARTH_RADIUS_M = 6371000.0
REFRACTION_K = 0.13
TOL_PCT = 2.0        # generous: discretization only, and it is one-sided


def build_flat_sea(tmp, res, extent_km):
    n = int(extent_km * 1000 / res)
    arr = np.zeros((n, n), dtype="float32")
    prof = dict(driver="GTiff", height=n, width=n, count=1, dtype="float32",
                crs="EPSG:32759", nodata=-9999.0,
                transform=from_origin(400000, 8000000, res, res))
    dem = os.path.join(tmp, "flat.tif")
    with rasterio.open(dem, "w", **prof) as d:
        d.write(arr, 1)
    x = 400000 + (n // 2) * res + res / 2
    y = 8000000 - (n // 2) * res - res / 2
    shp = os.path.join(tmp, "obs.shp")
    gpd.GeoDataFrame({"geometry": [sg.Point(x, y)]}, crs="EPSG:32759").to_file(shp)
    return dem, shp, n


def visible_radius(wbt, dem, shp, out, offset_a, offset_b, n, res):
    wbt.run_tool("ViewshedOffsets", [
        f"--dem={dem}", f"--stations={shp}", f"--output={out}",
        f"--offset_a={offset_a}", f"--offset_b={offset_b}",
        "--curvature", f"--refraction_k={REFRACTION_K}",
        f"--earth_radius={EARTH_RADIUS_M}",
    ])
    with rasterio.open(out) as d:
        a = d.read(1)
    rr, cc = np.nonzero(a == 1)
    if rr.size == 0:
        return 0.0
    c0 = n // 2
    return float(np.sqrt((rr - c0) ** 2 + (cc - c0) ** 2).max() * res)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--res", type=float, default=50.0)
    ap.add_argument("--extent-km", type=float, default=80.0)
    args = ap.parse_args()

    c = (1.0 - REFRACTION_K) / (2.0 * EARTH_RADIUS_M)
    wbt = whitebox.WhiteboxTools()
    wbt.set_verbose_mode(False)
    wbt.set_compress_rasters(False)

    tmp = tempfile.mkdtemp(prefix="horizon_")
    fails = []
    try:
        dem, shp, n = build_flat_sea(tmp, args.res, args.extent_km)
        out = os.path.join(tmp, "vs.tif")

        print(f"flat sea, {args.extent_km:.0f} km across, {args.res:.0f} m cells, "
              f"k={REFRACTION_K}\n")

        print("1. Horizon to the sea surface:  d = sqrt(h/c)")
        print(f"   {'eye height':>11} {'measured':>10} {'analytic':>10} {'err':>7}")
        for h in (1.0, 1.7, 3.0, 5.0, 10.0, 25.0):
            meas = visible_radius(wbt, dem, shp, out, h, 0.0, n, args.res)
            ana = np.sqrt(h / c)
            err = 100 * (meas - ana) / ana
            ok = abs(err) <= TOL_PCT
            print(f"   {h:>10.1f}m {meas/1000:>9.2f}km {ana/1000:>9.2f}km "
                  f"{err:>6.1f}% {'' if ok else ' FAIL'}")
            if not ok:
                fails.append(f"horizon h={h}")

        print("\n2. Two-height mutual visibility:  d = sqrt(h1/c) + sqrt(h2/c)")
        print("   (this is what fixes the magnitude of --offset_b)")
        h1 = 1.7
        print(f"   {'target h':>11} {'measured':>10} {'analytic':>10} {'err':>7}")
        for h2 in (1.7, 5.0, 15.0, 25.0):
            meas = visible_radius(wbt, dem, shp, out, h1, h2, n, args.res)
            ana = np.sqrt(h1 / c) + np.sqrt(h2 / c)
            err = 100 * (meas - ana) / ana
            ok = abs(err) <= TOL_PCT
            print(f"   {h2:>10.1f}m {meas/1000:>9.2f}km {ana/1000:>9.2f}km "
                  f"{err:>6.1f}% {'' if ok else ' FAIL'}")
            if not ok:
                fails.append(f"sum-rule h2={h2}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if fails:
        print(f"GATE 5 FAILED: {', '.join(fails)}")
        sys.exit(1)
    print("GATE 5 PASSED - curvature and offsets match closed-form geometry")


if __name__ == "__main__":
    main()
