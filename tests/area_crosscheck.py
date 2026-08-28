#!/usr/bin/env python3
"""Does an independent engine reproduce the AREA RATIO that --offset_b produces?

`cross_engine.py` compares the two engines cell by cell, and finds that on the
most marginal cells -- those whose visibility a 1.7 m offset changes at all --
the plugin is more permissive than a converged transect-based LOS test. That
is expected: WhiteboxTools' sweep interpolates its horizon along eight facets,
which is an approximation, and the cells in question sit exactly on the
visibility boundary where any approximation shows.

But per-cell disagreement on a hand-picked marginal population says nothing
about whether the headline number -- the percentage by which offset_b enlarges
a viewshed -- is right. Both engines could be permissive in the same proportion
at both offsets, leaving the RATIO intact. This test measures that directly.

Method: stratified estimation. The plugin partitions every cell in the 50 km
disc into three strata whose sizes are known exactly:

    both_hidden    hidden at offset_b=0 and at 1.7
    both_visible   visible at both
    flipped        hidden at 0, visible at 1.7

The LOS witness's visible count at each offset is then

    sum over strata of  (stratum size) x P(los says visible | stratum)

with the conditional probabilities estimated from the sampled pairs at a
converged transect density. The ratio of the two estimates is an independent
measurement of what offset_b does to viewshed AREA, which is the number any
downstream figure would rest on.

Usage:
    python tests/area_crosscheck.py --stage sizes
    python tests/area_crosscheck.py --stage los      # needs WBTVO_LOS_MODULE
    python tests/area_crosscheck.py --stage report

Each stage may run under a different interpreter if the LOS witness and
rasterio/geopandas cannot share one.
"""

import argparse
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
import config

DEM = config.DEM
PAIRS_CSV = os.path.join(HERE, "cross_engine_pairs.csv")
SIZES_JSON = os.path.join(HERE, "area_crosscheck_sizes.json")
LOS_CSV = os.path.join(HERE, "area_crosscheck_los.csv")
HEIGHT_A = 1.7
OFFSET_B = 1.7
CLIP_RADIUS_M = 50000.0
N_TRANSECT = 5000     # converged: 5 m spacing at 25 km, finer than the 10 m DEM


def stage_sizes():
    """Exact stratum sizes per station, inside the disc, from the plugin rasters."""
    import numpy as np
    import rasterio

    out = {}
    for sid, stem, _shp in config.stations():
        p0, p1 = config.reference_paths(stem)
        with rasterio.open(p0) as d0, rasterio.open(p1) as d1:
            a0, a1 = d0.read(1), d1.read(1)
            nd0, nd1 = d0.nodata, d1.nodata
            tr = d0.transform
            res = abs(tr.a)

        valid = np.ones(a0.shape, bool)
        if nd0 is not None:
            valid &= a0 != nd0
        if nd1 is not None:
            valid &= a1 != nd1

        # Same pixel-index disc used when tallying the reference viewsheds.
        lr, lc = a0.shape[0] // 2, a0.shape[1] // 2
        rr = (np.arange(a0.shape[0])[:, None] - lr).astype(np.int64)
        cc = (np.arange(a0.shape[1])[None, :] - lc).astype(np.int64)
        disc = (rr ** 2 + cc ** 2) <= (CLIP_RADIUS_M / res) ** 2
        m = valid & disc

        v0, v1 = (a0 == 1) & m, (a1 == 1) & m
        out[sid] = {
            "both_hidden": int((m & ~v0 & ~v1).sum()),
            "both_visible": int((v0 & v1).sum()),
            "flipped": int((v1 & ~v0).sum()),
            "lost": int((v0 & ~v1).sum()),
            "plugin_visible_b0": int(v0.sum()),
            "plugin_visible_b170": int(v1.sum()),
            "cell_area_km2": (res * res) / 1e6,
        }
        s = out[sid]
        print(f"  {sid:<9} hidden {s['both_hidden']:>10,}  visible {s['both_visible']:>9,}"
              f"  flipped {s['flipped']:>9,}  lost {s['lost']:>3,}")
    json.dump(out, open(SIZES_JSON, "w"), indent=1)
    print(f"\nwrote {SIZES_JSON}")


def stage_los():
    """LOS-witness verdicts at both offsets, at a converged transect density."""
    lu = config.los_module()

    ds, gt, band, nodata = lu.open_dem(DEM)
    rows = list(csv.DictReader(open(PAIRS_CSV)))
    out = []
    for i, r in enumerate(rows, 1):
        ox, oy = float(r["obs_x"]), float(r["obs_y"])
        tx, ty = float(r["tgt_x"]), float(r["tgt_y"])
        rec = {"station": r["station"], "stratum": r["stratum"], "dist_m": r["dist_m"]}
        for ob, key in ((0.0, "los_b0"), (OFFSET_B, "los_b170")):
            res = lu.point_to_point_los(band, gt, nodata, ox, oy, HEIGHT_A,
                                        tx, ty, ob, n=N_TRANSECT,
                                        apply_curvature=True)
            rec[key] = int(res["visible"])
        out.append(rec)
        if i % 500 == 0:
            print(f"  {i}/{len(rows)}", flush=True)

    with open(LOS_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
        w.writeheader()
        w.writerows(out)
    print(f"wrote {LOS_CSV}  (n_transect={N_TRANSECT})")


def stage_report():
    import numpy as np

    sizes = json.load(open(SIZES_JSON))
    rows = list(csv.DictReader(open(LOS_CSV)))

    print(f"Independent area estimate from the LOS witness, transect n={N_TRANSECT}")
    print("(stratified: exact plugin stratum sizes x sampled LOS-witness rates)\n")
    print(f"{'station':<9} {'plugin +%':>10} {'los +%':>13} {'ratio':>8}  "
          f"{'n_samp':>7}")
    print("-" * 54)

    ratios = []
    for sid in sizes:
        s = sizes[sid]
        est = {}
        n_used = 0
        for off in ("los_b0", "los_b170"):
            total = 0.0
            for stratum in ("both_hidden", "both_visible", "flipped"):
                samp = [int(r[off]) for r in rows
                        if r["station"] == sid and r["stratum"] == stratum]
                if not samp:
                    continue
                p = float(np.mean(samp))
                total += s[stratum] * p
                if off == "los_b0":
                    n_used += len(samp)
            est[off] = total

        plugin_pct = 100 * (s["plugin_visible_b170"] - s["plugin_visible_b0"]) / s["plugin_visible_b0"]
        if est["los_b0"] > 0:
            los_pct = 100 * (est["los_b170"] - est["los_b0"]) / est["los_b0"]
            ratio = (1 + los_pct / 100) / (1 + plugin_pct / 100)
        else:
            los_pct, ratio = float("nan"), float("nan")
        ratios.append(ratio)
        print(f"{sid:<9} {plugin_pct:>9.1f}% {los_pct:>12.1f}% {ratio:>8.3f}  {n_used:>7}")

    print("-" * 54)
    r = np.array([x for x in ratios if np.isfinite(x)])
    print(f"\nratio = (LOS growth) / (plugin growth); 1.000 means the two "
          f"engines\nagree on what the target offset does to AREA.")
    print(f"  mean {r.mean():.3f}   range {r.min():.3f}-{r.max():.3f}")
    if abs(r.mean() - 1.0) < 0.15:
        print("\nVERDICT: the area effect is confirmed by an independent engine.")
    else:
        print("\nVERDICT: the engines DISAGREE on the area effect - investigate.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["sizes", "los", "report"], required=True)
    a = ap.parse_args()
    {"sizes": stage_sizes, "los": stage_los, "report": stage_report}[a.stage]()
