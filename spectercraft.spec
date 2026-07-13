# -*- mode: python ; coding: utf-8 -*-

import platform

system = platform.system()

if system == "Windows":
    MEI_TEMP = '%APPDATA%\\SpecterCraft\\_runtime'
elif system == "Darwin":
    MEI_TEMP = '~/Library/Application Support/SpecterCraft/_runtime'
else:
    MEI_TEMP = '~/.spectercraft/_runtime'

a = Analysis(
    ['launcher/main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('assets', 'assets'),
    ],
    hiddenimports=['clientnewcode'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

if system == "Darwin":
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name='SpecterCraft',
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
        icon=None,
    )

    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=True,
        upx_exclude=[],
        name='SpecterCraft',
    )

    app = BUNDLE(
        coll,
        name='SpecterCraft.app',
        icon=None,
        bundle_identifier='com.spectercraft.launcher',
        info_plist={
            'CFBundleName': 'SpecterCraft',
            'CFBundleDisplayName': 'SpecterCraft',
            'CFBundleVersion': '1.0.0',
            'CFBundleShortVersionString': '1.0.0',
            'NSHighResolutionCapable': True,
        },
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name='SpecterCraft',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        upx_exclude=[],
        runtime_tmpdir=MEI_TEMP,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=None,
    )
