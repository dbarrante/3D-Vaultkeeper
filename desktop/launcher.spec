# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

repo_root = Path(SPECPATH).resolve().parent

a = Analysis(
    ['launcher.py'],
    pathex=[str(repo_root / 'backend')],
    binaries=[],
    datas=[
        (str(repo_root / 'frontend' / 'dist'), 'frontend_dist'),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
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
    name='3D Vaultkeeper',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='3D Vaultkeeper',
)
