#!/usr/bin/env python
"""Build a standalone ytm2j executable for the current platform.

    python scripts/build.py

Requires:  pip install pyinstaller

Run it once on Windows, once on macOS and once on Linux to produce native
single-file executables in ./dist. PyInstaller cannot cross-compile, so each
platform needs its own build machine (or CI runner).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
WORK = ROOT / "build"


def main() -> int:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        sys.exit("PyInstaller is not installed. Run:  pip install pyinstaller")

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--clean", "--noconfirm", "--onefile",
        "--distpath", str(DIST),
        "--workpath", str(WORK / "pyinstaller"),
        "--specpath", str(WORK),
        str(ROOT / "scripts" / "ytm2j.spec"),
    ]
    print("Running:", " ".join(cmd))
    subprocess.check_call(cmd, cwd=str(ROOT))
    print(f"\nBuild complete. Look for the executable in: {DIST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
