# floodlight-gui.spec -- PyInstaller build spec for the desktop app.
#
# Build:  poetry run pyinstaller floodlight-gui.spec --noconfirm
# Output: dist/floodlight-gui/  (one-dir bundle; the launcher is the
#         floodlight-gui[.exe] inside it)
#
# Why the explicit collection below: the app loads floodlight model/IO classes
# by dotted string via importlib (registry class_path), and resolves its own
# tab/engine submodules dynamically, so PyInstaller's static analysis misses
# them. We also bundle Dear PyGui's native library, imageio-ffmpeg's bundled
# ffmpeg (for video export), and the pitch PNG assets.

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

datas = []
binaries = []
hiddenimports = []

# Packages with native libs / data / dynamic imports that need full collection.
# numpy/pandas/scipy/matplotlib are handled by PyInstaller's built-in hooks.
for _pkg in ("floodlight", "dearpygui", "imageio_ffmpeg"):
    _d, _b, _h = collect_all(_pkg)
    datas += _d
    binaries += _b
    hiddenimports += _h

# The app itself: every submodule (registry/engine use importlib) + the assets.
hiddenimports += collect_submodules("floodlight_gui")
datas += collect_data_files("floodlight_gui")

a = Analysis(
    ["src/floodlight_gui/__main__.py"],
    pathex=["src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="floodlight-gui",
    debug=False,
    strip=False,
    upx=False,
    console=False,  # windowed GUI app; flip to True temporarily to debug a launch crash
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="floodlight-gui",
)
