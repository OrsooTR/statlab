# PyInstaller build spec for the StatLab desktop executable (one-dir bundle).
# Build:  pyinstaller statlab.spec --noconfirm
# Output: dist/StatLab/StatLab.exe  (zip the StatLab folder for distribution)
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = [
    ("app/static", "app/static"),
    ("app/crazytime/wheel_config.json", "app/crazytime"),
    ("app/football/competitions.json", "app/football"),
]
binaries = []
hiddenimports = (
    collect_submodules("uvicorn")
    + collect_submodules("webview")
    + ["sklearn.neighbors._partition_nodes", "sklearn.utils._typedefs",
       "sklearn.utils._heap", "sklearn.utils._sorting", "sklearn.utils._vector_sentinel",
       "scipy._cyutility", "engineio.async_drivers.threading"]
)
for pkg in ("sklearn", "scipy", "pandas", "reportlab", "openpyxl"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    ["run_desktop.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "IPython", "jupyter", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="StatLab",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="StatLab",
)
