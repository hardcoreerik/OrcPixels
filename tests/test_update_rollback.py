"""OTA update fix (2026-10-15): 0.2.3 -> 0.2.4 "Update now" rolled back.

Setup's log showed Restart Manager finding OrcPixels.exe (still shutting down) and
chrome-native-host.exe (a browser helper that had loaded our _internal\\VCRUNTIME140.dll
through PyInstaller's inherited DLL directory); it couldn't close them and, silent,
aborted. Covers: install-log parsing + startup reconcile, /WAITPID, log rotation, the
exit watchdog, the DLL-search reset for child processes, and the .iss guards."""
import re
import threading
from pathlib import Path

import pytest

from orcpixels import paths
from orcpixels import settings as S
from orcpixels import updater as UP
from orcpixels import winproc

ROOT = Path(__file__).resolve().parents[1]

# Setup log from the failed update (paths shortened)
ROLLED_BACK = r"""2026-10-15 09:07:17.524   Log opened. (Time zone: UTC-07:00)
2026-10-15 09:07:17.524   Setup version: Inno Setup version 6.7.1
2026-10-15 09:07:17.524   Setup command line: /SL5="$4C09B8,25706281,844800,C:\Temp\OrcPixels-Setup-0.2.4.exe" /SILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS /RELAUNCH=1 /DIR="C:\Program Files\OrcPixels" /ALLUSERS
2026-10-15 09:07:17.527   Administrative install mode: Yes
2026-10-15 09:07:17.607   Found 54 files to register with RestartManager.
2026-10-15 09:07:17.607   Calling RestartManager's RmGetList.
2026-10-15 09:07:17.805   RmGetList finished successfully.
2026-10-15 09:07:17.805   RestartManager found an application using one of our files: OrcPixels.exe
2026-10-15 09:07:17.805   RestartManager found an application using one of our files: chrome-native-host.exe
2026-10-15 09:07:17.805   Can use RestartManager to avoid reboot? Yes (0)
2026-10-15 09:07:17.826   Starting the installation process.
2026-10-15 09:07:17.828   Shutting down applications using our files.
2026-10-15 09:07:18.004   Some applications could not be shut down.
2026-10-15 09:07:18.005   Defaulting to Abort for suppressed message box (Abort/Retry/Ignore):
                          Setup was unable to automatically close all applications. It is recommended that you close all applications using files that need to be updated by Setup before continuing.
2026-10-15 09:07:18.005   User canceled the installation process.
2026-10-15 09:07:18.005   Rolling back changes.
2026-10-15 09:07:18.009   Starting the uninstallation process.
2026-10-15 09:07:18.009   Uninstallation process succeeded.
2026-10-15 09:07:19.524   Deinitializing Setup.
2026-10-15 09:07:19.531   Log closed.
"""

SUCCEEDED = """2026-10-14 22:07:30.600   Log opened. (Time zone: UTC-07:00)
2026-10-14 22:07:31.000   Starting the installation process.
2026-10-14 22:07:34.900   Installation process succeeded.
2026-10-14 22:07:35.100   Need to restart Windows? No
2026-10-14 22:07:35.200   Log closed.
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "OUT", tmp_path / "out")
    monkeypatch.setattr(S, "PATH", tmp_path / "settings.json")
    monkeypatch.setattr(S, "INSTALL", tmp_path / "install.json")
    monkeypatch.setenv("ORCPIXELS_UPDATE_MODE", "installed")
    monkeypatch.setattr(UP, "__version__", "0.2.3")
    (tmp_path / "out").mkdir()
    return tmp_path


def test_parse_rolled_back_log():
    r = UP.parse_install_log(ROLLED_BACK)
    assert r["ok"] is False and r["reason"] == "files in use"
    assert r["blockers"] == ["OrcPixels.exe", "chrome-native-host.exe"]
    assert "chrome-native-host.exe" in r["detail"]


def test_parse_success_and_unfinished_logs():
    assert UP.parse_install_log(SUCCEEDED)["ok"] is True
    assert UP.parse_install_log("")["ok"] is None
    assert UP.parse_install_log("2026-10-15 09:00:00.000   Log opened.\n")["ok"] is None


def test_reconcile_failed_update(env):
    UP._set_install_state(state="installing", version="0.2.4")
    UP.install_log().write_text(ROLLED_BACK, encoding="utf-8")
    inst = UP.reconcile_install()
    assert inst["state"] == "failed" and inst["blockers"] == ["OrcPixels.exe", "chrome-native-host.exe"]
    assert "v0.2.4 did not install" in inst["error"] and "Still on v0.2.3" in inst["error"]
    assert UP.reconcile_install() is None                     # only once


def test_reconcile_installed_update(env, monkeypatch):
    UP._set_install_state(state="installing", version="0.2.4")
    UP.install_log().write_text(SUCCEEDED, encoding="utf-8")
    monkeypatch.setattr(UP, "__version__", "0.2.4")
    assert UP.reconcile_install()["state"] == "done"


def test_reconcile_rolled_back_but_files_replaced(env, monkeypatch):
    UP._set_install_state(state="installing", version="0.2.4")
    UP.install_log().write_text(ROLLED_BACK, encoding="utf-8")
    monkeypatch.setattr(UP, "__version__", "0.2.4")           # mixed install after a late rollback
    inst = UP.reconcile_install()
    assert inst["state"] == "failed" and "repair" in inst["error"]


def test_reconcile_nothing_pending(env):
    assert UP.reconcile_install() is None


def test_install_log_rotates(env):
    UP.install_log().write_text("old attempt", encoding="utf-8")
    UP._rotate_install_log()
    assert not UP.install_log().exists()
    assert (paths.OUT / "update-install.prev.log").read_text() == "old attempt"


def test_installer_args_wait_for_this_process(env, monkeypatch, tmp_path):
    monkeypatch.setattr(UP, "needs_admin", lambda d=None: True)
    args = UP.installer_args(tmp_path, wait_pid=4242)
    assert "/WAITPID=4242" in args and "/ALLUSERS" in args and "/CLOSEAPPLICATIONS" in args
    assert any(a.startswith("/LOG=") and "update-install.log" in a for a in args)


def test_exit_watchdog_hard_exits_when_shutdown_hangs():
    hit, release = [], threading.Event()
    done = threading.Event()

    def hard_exit(code):
        hit.append(code)
        done.set()
    t = threading.Thread(target=UP._exit_for_update,
                         args=(lambda: release.wait(5), hard_exit, 0.05, True), daemon=True)
    t.start()
    assert done.wait(3) and hit == [0]
    release.set()
    t.join(5)


def test_exit_watchdog_on_shutdown_error_and_disarmed_in_source_mode():
    hit = []

    def boom():
        raise RuntimeError("x")
    UP._exit_for_update(boom, hit.append, 30, True)
    assert hit == [0]
    hit.clear()
    UP._exit_for_update(lambda: None, hit.append, 0.01, False)   # not frozen: no watchdog
    threading.Event().wait(0.1)
    assert hit == []


class FakeK32:
    def __init__(self):
        self.calls = []

    def SetDllDirectoryW(self, p):
        self.calls.append(p)
        return 1


def test_clean_dll_search_resets_and_restores():
    k = FakeK32()
    with winproc.clean_dll_search(k, home=r"C:\Program Files\OrcPixels\_internal"):
        assert k.calls == [None]
    assert k.calls == [None, r"C:\Program Files\OrcPixels\_internal"]
    k2 = FakeK32()
    with pytest.raises(ValueError):
        with winproc.clean_dll_search(k2, home="H"):
            raise ValueError
    assert k2.calls == [None, "H"]                                # restored on error too


def test_clean_dll_search_noop_when_not_frozen():
    assert winproc._kernel32() is None                           # tests never run frozen
    with winproc.clean_dll_search():
        pass


def test_tray_opens_browser_through_winproc():
    src = (ROOT / "orcpixels" / "tray.py").read_text(encoding="utf-8")
    assert "webbrowser.open(" not in src and "open_url(" in src


def _iss() -> str:
    return (ROOT / "packaging" / "OrcPixels.iss").read_text(encoding="utf-8")


def test_iss_restart_manager_only_checks_our_exes():
    s = _iss()
    assert re.search(r"^CloseApplicationsFilter=\*\.exe\s*$", s, re.M)
    assert re.search(r"^CloseApplications=force\s*$", s, re.M)


def test_iss_runtime_dlls_are_version_checked_not_forced():
    s = _iss()
    main = next(l for l in s.splitlines() if l.startswith('Source: "{#SrcDir}\\*"'))
    assert "Excludes:" in main and "ignoreversion" in main
    for dll in ("VCRUNTIME140*.dll", "MSVCP140*.dll", "ucrtbase.dll", "api-ms-win-*.dll"):
        assert dll in s.split("#define RuntimeDlls", 1)[1].splitlines()[0]
        line = next(l for l in s.splitlines() if l.startswith(f'Source: "{{#SrcDir}}\\{dll}"'))
        assert "ignoreversion" not in line and "restartreplace" in line


def test_iss_waits_for_app_and_relaunches_after_failure():
    s = _iss()
    assert "{param:WAITPID|0}" in s and "WaitForSingleObject" in s
    assert re.search(r"function InitializeSetup\(\): Boolean;\s*begin\s*InstallDone := False;\s*WaitForApp\(\);", s)
    assert "procedure DeinitializeSetup" in s and "ExecAsOriginalUser" in s
    assert "CurStep = ssDone" in s
