"""Test data configuration.

Most tests in this directory need real terrain: a DEM and a set of viewing
stations. Those are not in the repository, and the identity of the stations is
deliberately not either — station locations are often restricted information
(protected sites, private infrastructure, sensitive habitat), so the tests
discover whatever station files they are pointed at and report them under
neutral labels (Station A, Station B, ...) assigned in sorted filename order.

Point the tests at your own data with environment variables:

    WBTVO_DEM         path to a DEM raster
    WBTVO_STATIONS    directory of single-point station vector files
    WBTVO_SUFFIX      station filename suffix        (default "_obs.shp")

Two tests compare against viewsheds produced by some other pipeline, and need
to know how those files are named. The patterns take a single `{id}` field,
filled with the station filename stem:

    WBTVO_REF_DIR     directory holding the reference viewsheds
    WBTVO_REF_PATTERN reference at target offset 0   e.g. "viewshed_{id}.tif"
    WBTVO_OFFB_PATTERN reference at the raised offset e.g. "viewshed_{id}_b170.tif"

Two tests (`cross_engine.py`, `area_crosscheck.py`) also need an independent
point-to-point line-of-sight implementation to act as a witness. It is not in
the repository either — the point is that it be independent of this code. Give
the path to a Python module that exposes:

    WBTVO_LOS_MODULE  path to a .py file providing

        open_dem(path) -> (dataset, geotransform, band_array, nodata)
        point_to_point_los(band, gt, nodata, ox, oy, offset_a,
                           tx, ty, offset_b, n=<transect samples>,
                           apply_curvature=<bool>) -> {"visible": bool, ...}

    where (ox, oy) and (tx, ty) are observer and target coordinates in the
    DEM's CRS, offsets are heights above ground in z units, and `n` is the
    number of elevation samples interpolated along the transect.

`tests/horizon_analytic.py` needs none of this — it builds its own DEM.
"""

import glob
import os
import string
import sys

DEM = os.environ.get("WBTVO_DEM", "")
STATIONS_DIR = os.environ.get("WBTVO_STATIONS", "")
SUFFIX = os.environ.get("WBTVO_SUFFIX", "_obs.shp")
REF_DIR = os.environ.get("WBTVO_REF_DIR", "")
REF_PATTERN = os.environ.get("WBTVO_REF_PATTERN", "viewshed_{id}.tif")
OFFB_PATTERN = os.environ.get("WBTVO_OFFB_PATTERN", "viewshed_{id}_b170.tif")
LOS_MODULE = os.environ.get("WBTVO_LOS_MODULE", "")


def require(*names):
    """Exit with a usable message rather than a traceback if data is unset."""
    missing = [n for n in names if not globals().get(n)]
    if not missing:
        return
    env = {"DEM": "WBTVO_DEM", "STATIONS_DIR": "WBTVO_STATIONS",
           "REF_DIR": "WBTVO_REF_DIR", "LOS_MODULE": "WBTVO_LOS_MODULE"}
    lines = [f"  {env.get(m, m)}" for m in missing]
    raise SystemExit(
        "This test needs real terrain data, which is not in the repository.\n"
        "Set:\n" + "\n".join(lines) + "\n\n"
        "See tests/config.py. tests/horizon_analytic.py needs no data and can "
        "be run as-is."
    )


def stations():
    """Discover station files. Returns [(label, stem, path), ...].

    `label` is neutral and safe to print. `stem` is the real filename stem and
    is used only to build reference-raster paths — never displayed.
    """
    require("DEM", "STATIONS_DIR")
    paths = sorted(glob.glob(os.path.join(STATIONS_DIR, f"*{SUFFIX}")))
    if not paths:
        raise SystemExit(
            f"no station files matching *{SUFFIX} in {STATIONS_DIR}\n"
            f"(set WBTVO_SUFFIX if yours are named differently)")
    out = []
    for i, p in enumerate(paths):
        stem = os.path.basename(p)[: -len(SUFFIX)] if SUFFIX else os.path.basename(p)
        out.append((f"Station {_label(i)}", stem, p))
    return out


def _label(i):
    letters = string.ascii_uppercase
    if i < len(letters):
        return letters[i]
    return f"{letters[i // len(letters) - 1]}{letters[i % len(letters)]}"


def reference_paths(stem):
    """(offset_b=0 reference, raised-offset reference) for a station stem."""
    require("REF_DIR")
    return (os.path.join(REF_DIR, REF_PATTERN.format(id=stem)),
            os.path.join(REF_DIR, OFFB_PATTERN.format(id=stem)))


def los_module():
    """Import the independent LOS witness named by WBTVO_LOS_MODULE."""
    require("LOS_MODULE")
    import importlib.util

    spec = importlib.util.spec_from_file_location("wbtvo_los_witness", LOS_MODULE)
    if spec is None or spec.loader is None:
        raise SystemExit(f"WBTVO_LOS_MODULE is not an importable .py file: {LOS_MODULE}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for name in ("open_dem", "point_to_point_los"):
        if not callable(getattr(mod, name, None)):
            raise SystemExit(f"{LOS_MODULE} must define {name}(); see tests/config.py")
    return mod


def station_xy(path):
    import geopandas as gpd

    g = gpd.read_file(path)
    pt = g.geometry.iloc[0]
    return float(pt.x), float(pt.y)
