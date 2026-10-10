"""Frozen entry point (PyInstaller): the tray app. `OrcPixels.exe serve ...` / other CLI
subcommands still work for troubleshooting."""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    if len(sys.argv) > 1 and sys.argv[1] not in ("--no-browser",):
        from orcpixels.cli import main
        raise SystemExit(main())
    from orcpixels.tray import main
    raise SystemExit(main())
