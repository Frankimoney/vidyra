#!/usr/bin/env python3
"""Strip Debian-managed Python packages from the image's dist-packages.

The RunPod CUDA base installs a handful of libraries via apt (PyJWT, grpcio,
cryptography, ...) into /usr/lib/python3*/dist-packages. Debian drops the
``RECORD`` file, so when our backend requirements ask for a newer version pip
aborts with::

    error: uninstall-no-record-file
    x Cannot uninstall PyJWT 2.7.0 ... no RECORD file was found

We only want the pip-managed copies (which the app + ComfyUI agree on), so
remove every dist-info directory that lacks a RECORD, together with the module
files it owns. The subsequent ``pip install -r requirements.txt`` then installs
clean copies with no uninstall attempt.

This intentionally touches only Debian's ``dist-packages`` trees, never the
pip-managed ``site-packages``/``/usr/local`` trees.
"""

from __future__ import annotations

import glob
import os
import shutil
import sys

DEBIAN_DIRS = (
    "/usr/lib/python3/dist-packages",
    "/usr/lib/python3.10/dist-packages",
    "/usr/lib/python3.11/dist-packages",
    "/usr/lib/python3.12/dist-packages",
    "/usr/lib/python3.13/dist-packages",
)


def owned_modules(dist_info: str) -> list[str]:
    top = os.path.join(dist_info, "top_level.txt")
    if os.path.exists(top):
        with open(top, encoding="utf-8") as handle:
            return [line.strip() for line in handle if line.strip()]
    return []


def remove_module(base: str, name: str) -> None:
    for candidate in (
        os.path.join(base, name),
        os.path.join(base, f"{name}.py"),
        os.path.join(base, f"{name}.so"),
    ):
        if os.path.isdir(candidate):
            shutil.rmtree(candidate, ignore_errors=True)
            print(f"  removed dir  {candidate}")
        elif os.path.exists(candidate):
            os.remove(candidate)
            print(f"  removed file {candidate}")
    # Byte-compiled leftovers.
    for pyc in glob.glob(os.path.join(base, "__pycache__", f"{name}.*.pyc")):
        os.remove(pyc)


def main() -> int:
    cleaned = 0
    for base in DEBIAN_DIRS:
        if not os.path.isdir(base):
            continue
        for dist_info in sorted(glob.glob(os.path.join(base, "*.dist-info"))):
            if os.path.exists(os.path.join(dist_info, "RECORD")):
                continue
            name = os.path.basename(dist_info)[: -len(".dist-info")]
            print(f"Debian package without RECORD: {name}")
            for module in owned_modules(dist_info):
                remove_module(base, module)
            shutil.rmtree(dist_info, ignore_errors=True)
            cleaned += 1
        # Legacy egg-info metadata has the same uninstall problem.
        for egg_info in sorted(glob.glob(os.path.join(base, "*.egg-info"))):
            shutil.rmtree(egg_info, ignore_errors=True)
    print(f"Removed {cleaned} Debian dist-info record(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
