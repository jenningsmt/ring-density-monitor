# -*- mode: python ; coding: utf-8 -*-
# PyInstaller build spec. Build order (see README "Building a release"):
#   pip install -r requirements-build.lock
#   pyinstaller RingDensityMonitor.spec --clean
#   iscc installer.iss
#
# onedir (not onefile): avoids the self-extract-to-temp "dropper" pattern
# that onefile builds use, which is one of the more suspicious patterns to
# antivirus heuristics. No third-party runtime deps -- stdlib + tkinter only,
# so no hiddenimports/collect_all are needed here.
#
# datas: core/ring_baseline_library.py locates its galactic baseline JSON
# relative to its own __file__ (repo_root / "data" / "baselines" / ...).
# That resolution math still works when frozen, but only if the file is
# actually placed at the matching path inside the bundle -- PyInstaller
# does not do this automatically for plain data files, so it must be
# listed explicitly here. Only the galactic baseline is needed: this app
# always runs RingScorer with baseline=None (see core/ring_display.py's
# create_scorer()), so the sector-specific baselines under
# data/baselines/sectors/ are never read at runtime and are deliberately
# left out to keep the bundle small.
datas = [('data/baselines/galactic_ring_baselines.json', 'data/baselines')]

a = Analysis(
    ['app\\main.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='RingDensityMonitor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['icon.ico'],
    version='version_info.txt',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='RingDensityMonitor',
)
