# PyInstaller spec - builds a single-file `ytm2j` executable per platform.
# Usage:  python scripts/build.py
#
# Build the same way on each OS to produce native Windows (.exe), macOS
# (.app / binary) and Linux executables.

from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = []
binaries = []
hiddenimports = []

# yt-dlp, imageio-ffmpeg (bundled ffmpeg binary) and this package need
# their data files + native binaries collected.
for pkg in ("yt_dlp", "imageio_ffmpeg", "ytm2j"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# FastAPI / uvicorn are pure Python; just make sure the modules are seen.
hiddenimports += collect_submodules("fastapi")
hiddenimports += collect_submodules("uvicorn")

a = Analysis(
    ["ytm2j/__main__.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "PyQt5", "PySide2", "matplotlib", "numpy.testing"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ytm2j",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
