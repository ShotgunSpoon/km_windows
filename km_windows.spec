from PyInstaller.utils.hooks import collect_all
import os

datas = [("assets/sounds", "assets/sounds")]
binaries = []
hiddenimports = []
for package in ("prism", "av"):
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hiddenimports += h
a = Analysis(["km_gui.py"], pathex=[], binaries=binaries, datas=datas,
             hiddenimports=hiddenimports, hookspath=[], hooksconfig={}, runtime_hooks=[],
             excludes=["pytest", "IPython", "matplotlib", "pandas", "tkinter"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="km_windows_debug" if os.environ.get("KM_BUILD_CONSOLE") else "km_windows", debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=bool(os.environ.get("KM_BUILD_CONSOLE")),
          disable_windowed_traceback=False)
