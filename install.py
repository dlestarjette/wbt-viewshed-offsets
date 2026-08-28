#!/usr/bin/env python3
"""Install the viewshed_offsets plugin into an installed `whitebox` package.

WhiteboxTools 2.x discovers plugins at runtime: the core binary scans its
`plugins/` directory for `<name>.json` descriptors and spawns the matching
executable as a subprocess. So installing a third-party plugin is just copying
two files into that directory. There is no registration step and no license
check for MIT-licensed plugins.

The `whitebox` Python package keeps two copies of the plugins directory -- one
under `whitebox/WBT/plugins` (the pristine copy shipped with the download) and
one under `whitebox/plugins` (the working copy it chmods and runs from). Both
are written here, because `download_wbt()` re-copies WBT/ over the working copy
whenever it decides the install needs refreshing.

Usage:
    python install.py                  # install into the active interpreter's whitebox
    python install.py --dry-run
    python install.py --python /path/to/venv/bin/python
"""

import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EXE_NAME = "viewshed_offsets" + (".exe" if sys.platform == "win32" else "")
JSON_NAME = "viewshed_offsets.json"
BUILT = os.path.join(HERE, "target", "release", EXE_NAME)


def whitebox_dir(python_exe):
    """Locate the installed whitebox package, optionally under another interpreter."""
    code = "import whitebox, os; print(os.path.dirname(whitebox.__file__))"
    if python_exe:
        out = subprocess.run([python_exe, "-c", code], capture_output=True, text=True)
        if out.returncode != 0:
            raise SystemExit(f"could not import whitebox under {python_exe}:\n{out.stderr}")
        return out.stdout.strip()
    try:
        import whitebox
    except ImportError:
        raise SystemExit(
            "the `whitebox` package is not importable here. Either activate the "
            "environment that has it, or pass --python /path/to/that/python."
        )
    return os.path.dirname(whitebox.__file__)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--python", help="interpreter whose whitebox package to install into")
    ap.add_argument("--dry-run", action="store_true", help="report what would be copied and stop")
    args = ap.parse_args()

    if not os.path.exists(BUILT):
        raise SystemExit(f"binary not found at {BUILT}\nbuild it first: cargo build --release")

    pkg = whitebox_dir(args.python)
    targets = [os.path.join(pkg, "plugins"), os.path.join(pkg, "WBT", "plugins")]
    targets = [t for t in targets if os.path.isdir(os.path.dirname(t))]
    if not targets:
        raise SystemExit(f"no plugins directory found under {pkg}")

    for target in targets:
        os.makedirs(target, exist_ok=True)
        dst_exe = os.path.join(target, EXE_NAME)
        dst_json = os.path.join(target, JSON_NAME)
        if args.dry_run:
            print(f"would copy -> {dst_exe}\nwould copy -> {dst_json}")
            continue
        # Unlink before copying rather than overwriting in place. Writing over a
        # currently-executing binary corrupts it, and on Apple Silicon it also
        # invalidates the code signature so the file is killed on its next exec.
        # Removing first gives the new file its own inode and leaves any running
        # process holding the old one, which is what anyone reinstalling during a
        # long run expects. Learned the hard way: an in-place copy killed a
        # several-thousand-viewshed batch run more than halfway through.
        for dst in (dst_exe, dst_json):
            if os.path.exists(dst):
                os.unlink(dst)
        shutil.copy2(BUILT, dst_exe)
        shutil.copy2(os.path.join(HERE, JSON_NAME), dst_json)
        os.chmod(dst_exe, 0o755)
        print(f"installed -> {dst_exe}")
        print(f"installed -> {dst_json}")

    if not args.dry_run:
        print("\nVerify with:")
        print('  python -c "import whitebox; w=whitebox.WhiteboxTools(); '
              "print([t for t in w.list_tools().keys() if 'iewshed' in t])\"")


if __name__ == "__main__":
    main()
