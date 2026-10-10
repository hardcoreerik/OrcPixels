# PyInstaller spec: one-folder, windowed (no console) build of the OrcPixels tray app.
# Built by tools/build_installer.ps1; output dist/OrcPixels/ (OrcPixels.exe + _internal/).
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
datas, binaries, hidden = [], [], []
for pkg in ("bleak", "winrt", "uvicorn", "pystray"):
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hidden += h
    except Exception:
        pass
hidden += collect_submodules("orcpixels")
for src, dst in (("presets", "presets"), ("presets64", "presets64"), ("assets", "assets"),
                 ("profiles", "profiles"), ("orcpixels/static", "orcpixels/static"),
                 ("orcpixels/fonts", "orcpixels/fonts"), ("orcpixels/data", "orcpixels/data")):
    if os.path.isdir(os.path.join(ROOT, src)):
        datas.append((os.path.join(ROOT, src), dst))
for f in ("apps.json", "projects.json", "settings.example.json", "displays.example.json", "apps.local.example.json"):
    datas.append((os.path.join(ROOT, "config", f), "config"))
datas += [(os.path.join(ROOT, "tools", "pixfont.py"), "tools"), (os.path.join(ROOT, "LICENSE"), "."),
          (os.path.join(ROOT, "NOTICE"), ".")]

a = Analysis([os.path.join(SPECPATH, "orcpixels_tray.py")], pathex=[ROOT], binaries=binaries, datas=datas,
             hiddenimports=hidden, excludes=["pytest", "tkinter"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="OrcPixels", console=False,
          icon=os.path.join(SPECPATH, "orcpixels.ico"), version=None)
coll = COLLECT(exe, a.binaries, a.datas, name="OrcPixels")
