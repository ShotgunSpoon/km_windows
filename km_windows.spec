from PyInstaller.utils.hooks import collect_all
import os
from pathlib import Path
import prism

datas = [("assets/sounds", "assets/sounds")]
binaries = []
hiddenimports = []
for package in ("prism", "av"):
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hiddenimports += h
# Prism adds this directory to its package path at runtime. Frozen importers
# also need the extension at its import name, prism._prism_cffi.
binaries += [(str(Path(prism.__file__).parent / "_native" / "_prism_cffi.pyd"), "prism")]
a = Analysis(["km_gui.py"], pathex=[], binaries=binaries, datas=datas,
             hiddenimports=hiddenimports, hookspath=[], hooksconfig={}, runtime_hooks=[],
             excludes=["pytest", "IPython", "matplotlib", "pandas", "tkinter", "OpenGL"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="km_windows_debug" if os.environ.get("KM_BUILD_CONSOLE") else "km_windows", debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=bool(os.environ.get("KM_BUILD_CONSOLE")),
          disable_windowed_traceback=False)
