"""OrcPixels tray app (Windows build entry point; also works on Linux/macOS with pystray).

Starts the web server in a background thread (no console window in the installed build)
and shows a tray icon:  Open OrcPixels | Settings | Server address (click = copy) |
Start with Windows | Quit (stops the server cleanly: Live engines stop, BLE links close
through the normal shutdown path; nothing extra is ever sent to a sign).
"""
from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

from . import paths
from . import settings as S

log = logging.getLogger("orcpixels.tray")
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "OrcPixels"


# ------------------------------------------------------------------ autostart (per-user Run key)
def exe_command() -> str:
    if paths.FROZEN:
        return f'"{sys.executable}"'
    return f'"{sys.executable}" -m orcpixels tray'


def autostart_enabled() -> bool:
    if os.name != "nt":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_NAME)
            return True
    except OSError:
        return False


def set_autostart(on: bool) -> None:
    if os.name != "nt":
        raise OSError("autostart toggle is Windows-only")
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, exe_command())
        else:
            try:
                winreg.DeleteValue(k, RUN_NAME)
            except FileNotFoundError:
                pass


# ------------------------------------------------------------------ addresses
def lan_ips() -> list[str]:
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))           # no packet is sent (UDP connect picks a route)
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return sorted(i for i in ips if not i.startswith("127."))


def addresses(port: int, lan: bool) -> list[str]:
    out = [f"http://127.0.0.1:{port}"]
    if lan:
        out = [f"http://{ip}:{port}" for ip in lan_ips()] + out
    return out


def copy_text(text: str) -> None:
    if os.name == "nt":
        import subprocess
        subprocess.run(["clip"], input=text.encode("utf-16-le"), check=False,
                       creationflags=0x08000000)          # CREATE_NO_WINDOW
    else:
        try:
            import subprocess
            subprocess.run(["xclip", "-selection", "clipboard"], input=text.encode(), check=False)
        except OSError:
            pass


# ------------------------------------------------------------------ server thread
class Server:
    def __init__(self, port: int, lan: bool):
        self.port, self.lan = port, lan
        self.host = "0.0.0.0" if lan else "127.0.0.1"
        self.srv = None
        self.thread: threading.Thread | None = None

    def already_running(self) -> bool:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/displays", timeout=1.5)
            return True
        except Exception:  # noqa: BLE001
            return False

    def start(self) -> None:
        import uvicorn

        from .web import LanGuard, create_multi_app
        cfg = uvicorn.Config(LanGuard(create_multi_app()), host=self.host, port=self.port, log_level="info",
                             log_config=None)
        self.srv = uvicorn.Server(cfg)
        self.thread = threading.Thread(target=self.srv.run, name="orcpixels-server", daemon=True)
        self.thread.start()

    def stop(self, timeout: float = 20.0) -> None:
        if self.srv is not None:
            self.srv.should_exit = True             # lifespan shutdown: Live stops, BLE disconnects
        if self.thread is not None:
            self.thread.join(timeout)


def _logging() -> None:
    paths.OUT.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=str(paths.OUT / "server.log"), level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def icon_image():
    from PIL import Image
    p = paths.APP / "assets" / "orc_16.png"
    try:
        img = Image.open(p).convert("RGBA")
    except OSError:
        img = Image.new("RGBA", (16, 16), (40, 160, 60, 255))
    return img.resize((64, 64), Image.Resampling.NEAREST)


def build_menu(pystray, srv: Server, on_quit):
    url = f"http://127.0.0.1:{srv.port}"
    item, Menu = pystray.MenuItem, pystray.Menu

    def opener(path):
        return lambda icon, it: webbrowser.open(url + path)

    def copier(a):
        return lambda icon, it: copy_text(a)

    def toggle_autostart(icon, it):
        try:
            set_autostart(not autostart_enabled())
        except OSError as e:
            log.warning("autostart: %s", e)

    from . import updater as UP

    def upd_status():
        try:
            return UP.status()
        except Exception:  # noqa: BLE001
            return {}

    def upd_text(it):
        st = upd_status()
        return f"Update available v{st['latest']['version']}" if st.get("available") else "Up to date"

    def upd_now(icon, it):
        st = upd_status()
        if st.get("can_install"):
            webbrowser.open(url + "/#update")         # confirm + progress in the web UI
        else:
            webbrowser.open((st.get("latest") or {}).get("url") or UP.RELEASES_PAGE)

    def upd_check(icon, it):
        threading.Thread(target=lambda: (UP.check(True), icon.update_menu()), daemon=True).start()

    addr_items = [item(a + "  (copy)", copier(a)) for a in addresses(srv.port, srv.lan)]
    if not srv.lan:
        addr_items.append(item("LAN off (Settings > listen_lan)", None, enabled=False))
    return Menu(
        item("Open OrcPixels", opener("/"), default=True),
        item("Settings", opener("/#settings")),
        item("Server address", Menu(*addr_items)),
        Menu.SEPARATOR,
        item(upd_text, upd_now, visible=lambda it: bool(upd_status().get("available"))),
        item("Check for updates", upd_check),
        item("Start with Windows", toggle_autostart, checked=lambda it: autostart_enabled(),
             visible=os.name == "nt"),
        item("Quit", on_quit),
    )


def main(argv: list[str] | None = None) -> int:
    _logging()
    cfg = S.load()
    port = int(os.environ.get("ORCPIXELS_PORT") or cfg.get("port") or 8765)
    srv = Server(port, bool(cfg.get("listen_lan")))
    if srv.already_running():                       # second launch: just open the UI
        webbrowser.open(f"http://127.0.0.1:{port}")
        return 0
    srv.start()
    for _ in range(80):
        if srv.already_running():
            break
        time.sleep(0.25)
    if "--no-browser" not in (argv or sys.argv[1:]):
        webbrowser.open(f"http://127.0.0.1:{port}")
    try:
        import pystray
    except ImportError:
        log.warning("pystray not installed: running without a tray icon (Ctrl+C to stop)")
        try:
            while srv.thread and srv.thread.is_alive():
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        srv.stop()
        return 0

    def on_quit(icon, it):
        srv.stop()
        icon.stop()

    icon = pystray.Icon("OrcPixels", icon_image(), "OrcPixels", build_menu(pystray, srv, on_quit))
    from . import updater as UP
    UP.SHUTDOWN = lambda: on_quit(icon, None)        # "Update now": clean stop, the installer relaunches

    def refresh():                                   # pick up background check results
        while srv.thread and srv.thread.is_alive():
            time.sleep(60)
            try:
                icon.update_menu()
                st = UP.status()
                icon.title = f"OrcPixels {st['current']}" + (f" (update v{st['latest']['version']})" if st.get("available") else "")
            except Exception:  # noqa: BLE001
                pass
    threading.Thread(target=refresh, daemon=True).start()
    icon.run()
    srv.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
