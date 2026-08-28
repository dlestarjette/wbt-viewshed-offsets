#!/usr/bin/env python3
"""Gate 2: does the plugin's target offset agree with an independent engine?

This gate needs a point-to-point line-of-sight implementation that owes nothing
to this plugin and has always honored a target offset — an independent witness
for the one thing the parity gate cannot check: whether `--offset_b` does the
correct amount of work. Supply one via WBTVO_LOS_MODULE (interface in
tests/config.py).

The witness used for the recorded validation is genuinely independent. It
interpolates 500 elevation samples along a straight transect and adds a
curvature BULGE, (1-k)*d*(D-d)/(2R), to the intervening terrain. The plugin sweeps a grid of view angles and subtracts a
DROP from the observer, (1-k)*d^2/(2R), from the target. Different sampling,
different data structure, different algebraic form. So this also tests the claim
that the bulge and drop forms are the same geometry rearranged.

Perfect agreement is NOT expected. A 500-point interpolated transect and a
grid-cell sweep disagree near visibility boundaries by construction. The
acceptance criterion is that disagreement is SMALL and shows NO SYSTEMATIC
DIRECTION -- if the plugin's offset did too much or too little work, the
disagreement would be lopsided, and that is what this looks for.

The stages are separate commands so that the LOS witness and the plugin-side
dependencies (rasterio, geopandas) may live in different interpreters if they
must — a GDAL-bound witness and a rasterio environment do not always coexist.
Run each stage under whichever interpreter has what it needs.

Usage:
    python tests/cross_engine.py --stage a
    python tests/cross_engine.py --stage b      # needs WBTVO_LOS_MODULE
    python tests/cross_engine.py --stage c
"""

import argparse
import csv
import os
import sys

import config

DEM = config.DEM

HEIGHT_A = 1.7
OFFSET_B = 1.7
PAIRS_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cross_engine_pairs.csv")
PER_STRATUM = 150      # targets per stratum per station
SAMPLE_SEED = 20260815  # this file's own sampling only; touches no project RNG


def stage_a():
    """Sample target cells from the plugin's rasters, stratified by outcome."""
    import numpy as np
    import rasterio

    rng = np.random.default_rng(SAMPLE_SEED)
    rows = []
    for sid, stem, shp in config.stations():
        p0, p1 = config.reference_paths(stem)
        if not (os.path.exists(p0) and os.path.exists(p1)):
            print(f"  {sid}: SKIP (needs both reference rasters)")
            continue

        ox, oy = config.station_xy(shp)

        with rasterio.open(p0) as d0, rasterio.open(p1) as d1:
            a0, a1 = d0.read(1), d1.read(1)
            tr = d0.transform
            nd0, nd1 = d0.nodata, d1.nodata

        valid = np.ones(a0.shape, bool)
        if nd0 is not None:
            valid &= a0 != nd0
        if nd1 is not None:
            valid &= a1 != nd1

        v0, v1 = (a0 == 1) & valid, (a1 == 1) & valid
        strata = {
            "flipped": v1 & ~v0,          # the cells the offset revealed
            "both_visible": v1 & v0,
            "both_hidden": valid & ~v1 & ~v0,
        }

        for name, mask in strata.items():
            idx = np.flatnonzero(mask.ravel())
            if idx.size == 0:
                continue
            take = rng.choice(idx, size=min(PER_STRATUM, idx.size), replace=False)
            r, c = np.unravel_index(take, mask.shape)
            xs = tr.c + (c + 0.5) * tr.a
            ys = tr.f + (r + 0.5) * tr.e
            for rr, cc, tx, ty in zip(r, c, xs, ys):
                dist = float(np.hypot(tx - ox, ty - oy))
                if dist < 50.0:      # skip the observer's own cell
                    continue
                rows.append({
                    "station": sid, "stratum": name,
                    "obs_x": f"{ox:.3f}", "obs_y": f"{oy:.3f}",
                    "tgt_x": f"{tx:.3f}", "tgt_y": f"{ty:.3f}",
                    "dist_m": f"{dist:.2f}",
                    # Taken from the arrays the strata were built from, so the
                    # recorded verdict is by construction the one that put this
                    # cell in its stratum.
                    "plugin_vis_b0": int(bool(v0[rr, cc])),
                    "plugin_vis_b170": int(bool(v1[rr, cc])),
                })

    out = rows
    fields = ["station", "stratum", "obs_x", "obs_y", "tgt_x", "tgt_y", "dist_m",
              "plugin_vis_b0", "plugin_vis_b170"]
    with open(PAIRS_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in out:
            w.writerow({k: r[k] for k in fields})
    print(f"stage a: wrote {len(out)} pairs -> {PAIRS_CSV}")
    print("next:  python3 tests/cross_engine.py --stage b")


def stage_b():
    """Run the LOS witness point-to-point for the same pairs, both offsets."""
    lu = config.los_module()

    ds, gt, band, nodata = lu.open_dem(DEM)
    rows = list(csv.DictReader(open(PAIRS_CSV)))
    for i, r in enumerate(rows, 1):
        ox, oy = float(r["obs_x"]), float(r["obs_y"])
        tx, ty = float(r["tgt_x"]), float(r["tgt_y"])
        for ob, key in ((0.0, "los_vis_b0"), (OFFSET_B, "los_vis_b170")):
            res = lu.point_to_point_los(band, gt, nodata, ox, oy, HEIGHT_A,
                                        tx, ty, ob, apply_curvature=True)
            r[key] = int(res["visible"])
        if i % 500 == 0:
            print(f"  {i}/{len(rows)}", flush=True)

    fields = list(rows[0].keys())
    with open(PAIRS_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"stage b: added LOS-witness verdicts for {len(rows)} pairs")
    print("next:  python tests/cross_engine.py --stage c")


def stage_c():
    import numpy as np

    rows = list(csv.DictReader(open(PAIRS_CSV)))
    if not rows or "los_vis_b0" not in rows[0]:
        raise SystemExit("stage b has not run yet")

    def col(name):
        return np.array([int(r[name]) for r in rows])

    p0, p1 = col("plugin_vis_b0"), col("plugin_vis_b170")
    l0, l1 = col("los_vis_b0"), col("los_vis_b170")
    dist = np.array([float(r["dist_m"]) for r in rows])
    stratum = np.array([r["stratum"] for r in rows])

    print(f"{len(rows)} observer-target pairs, "
          f"{len(set(r['station'] for r in rows))} observers\n")

    print("Agreement between engines, by target offset:")
    for name, pv, lv in (("offset_b=0.0", p0, l0), (f"offset_b={OFFSET_B}", p1, l1)):
        agree = (pv == lv).mean()
        pl_more = int(((pv == 1) & (lv == 0)).sum())
        lo_more = int(((pv == 0) & (lv == 1)).sum())
        print(f"  {name:<16} {agree*100:6.2f}% agree   "
              f"plugin-only-visible {pl_more:>5}   los-only-visible {lo_more:>5}")

    print("\nThe flipped cells (plugin says the offset revealed them):")
    fl = stratum == "flipped"
    if fl.any():
        los_flipped = (l1[fl] == 1) & (l0[fl] == 0)
        los_vis_at_b170 = l1[fl] == 1
        print(f"  n = {int(fl.sum())}")
        print(f"  LOS witness also calls them visible at offset_b={OFFSET_B}: "
              f"{los_vis_at_b170.mean()*100:.2f}%")
        print(f"  LOS witness also calls them a flip (hidden at 0, visible at "
              f"{OFFSET_B}): {los_flipped.mean()*100:.2f}%")
        for lo, hi in ((0, 5000), (5000, 15000), (15000, 30000), (30000, 60000)):
            m = fl & (dist >= lo) & (dist < hi)
            if m.sum():
                agree = ((l1[m] == 1)).mean()
                print(f"    {lo/1000:>4.0f}-{hi/1000:<3.0f} km  n={int(m.sum()):>4}  "
                      f"LOS witness agrees visible {agree*100:5.1f}%")

    print("\nControl strata (the offset should NOT have changed these):")
    for name in ("both_visible", "both_hidden"):
        m = stratum == name
        if m.sum():
            print(f"  {name:<14} n={int(m.sum()):>4}  "
                  f"engines agree at offset_b={OFFSET_B}: "
                  f"{(p1[m] == l1[m]).mean()*100:5.2f}%")

    # Directional test: is disagreement lopsided?
    dis_pl = int(((p1 == 1) & (l1 == 0)).sum())
    dis_lo = int(((p1 == 0) & (l1 == 1)).sum())
    total = dis_pl + dis_lo
    print(f"\nDirectional test at offset_b={OFFSET_B}: "
          f"{dis_pl} vs {dis_lo} disagreements")
    if total == 0:
        print("  no disagreement at all")
        return
    skew = abs(dis_pl - dis_lo) / total
    print(f"  skew {skew*100:.1f}% "
          f"({'balanced -> sampling noise' if skew < 0.5 else 'LOPSIDED -> investigate'})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["a", "b", "c"], required=True)
    a = ap.parse_args()
    {"a": stage_a, "b": stage_b, "c": stage_c}[a.stage]()
