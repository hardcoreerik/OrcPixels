"""Command line interface: python -m orcpixels <command> ..."""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from PIL import Image

from . import protocol as P
from .ble_sign import DEFAULT_ADDRESS, SignClient, scan
from .render import compose_status, test_pattern


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="orcpixels", description="Control the iPixel Color BLE LED sign")
    p.add_argument("-a", "--address", default=DEFAULT_ADDRESS,
                   help=f"BLE address (default {DEFAULT_ADDRESS}); use '' to match by name prefix")
    p.add_argument("-v", "--verbose", action="count", default=0)
    rot = p.add_mutually_exclusive_group()
    rot.add_argument("--rotate", dest="rotate", action="store_true", default=None,
                     help="rotate output 180 deg for this run (default: config/settings.json)")
    rot.add_argument("--no-rotate", dest="rotate", action="store_false",
                     help="don't rotate for this run")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="list nearby LED_BLE_ signs")
    s.add_argument("-t", "--timeout", type=float, default=6.0)

    sub.add_parser("info", help="connect and print device info")

    t = sub.add_parser("send-text", help="native device text")
    t.add_argument("text")
    t.add_argument("-c", "--color", default="ffffff")
    t.add_argument("--bg", default=None)
    t.add_argument("--anim", default="static", help="static|scroll_left|scroll_right|blink|fade|snowflake or 0-7")
    t.add_argument("--speed", type=int, default=80)
    t.add_argument("--rainbow", type=int, default=0)

    i = sub.add_parser("image", help="show an image file (fit to 64x16)")
    i.add_argument("file")

    sub.add_parser("test-image", help="show built-in 64x16 test pattern (icon + text)")

    sp = sub.add_parser("splash", help="orc icon + scrolling 'OrcPixels' (animated GIF)")
    sp.add_argument("text", nargs="?", default="OrcPixels")

    pj = sub.add_parser("project", help="show a project's branding frame from config/projects.json")
    pj.add_argument("key")
    pj.add_argument("--kind", default="default",
                    help="default (project's default_preset, may be animated) | banner | wave | icon | <preset>")

    pr = sub.add_parser("preset", help="replay a saved preset from presets/ (no name = list)")
    pr.add_argument("name", nargs="?")

    cf = sub.add_parser("config", help="show or persist settings, e.g. 'config rotate_180 off'")
    cf.add_argument("key", nargs="?")
    cf.add_argument("value", nargs="?")

    g = sub.add_parser("gif", help="send an animated GIF file (should be 64x16)")
    g.add_argument("file")

    st = sub.add_parser("status", help="show icon + title/subtitle frame (phase-2 layout)")
    st.add_argument("title")
    st.add_argument("subtitle", nargs="?", default="")
    st.add_argument("--icon", default=None)

    b = sub.add_parser("brightness", help="0..100")
    b.add_argument("level", type=int)

    pw = sub.add_parser("power", help="on|off")
    pw.add_argument("state", choices=["on", "off"])

    sub.add_parser("clear", help="blank display (sends black frame)")

    wa = sub.add_parser("watch", help="AI status: follow the foreground Codex/Claude/Grok app (+ buddy lines)")
    wa.add_argument("--buddy", action="store_true", help="also show what the agent is doing (alternating)")
    wa.add_argument("--dry-run", action="store_true", help="print what would be sent; no BLE")
    wa.add_argument("--seconds", type=float, default=0, help="stop after N seconds (default: run until Ctrl+C)")
    wa.add_argument("--interval", type=float, default=1.0, help="poll period (default 1 s)")
    wa.add_argument("--preview-dir", default=None, help="write a 10x PNG of every (would-be) sent frame here")

    pl = sub.add_parser("playlist", help="on-device playlists: list | show <name> | save <name> <preset>... | "
                                         "upload <name> | play [name] | slots")
    pl.add_argument("action", choices=["list", "show", "save", "upload", "play", "slots"])
    pl.add_argument("name", nargs="?")
    pl.add_argument("presets", nargs="*", help="(save) preset names, optional ':seconds' suffix")
    pl.add_argument("--base-slot", type=int, default=None, help="(save) first device slot (default 1)")
    pl.add_argument("--skip-unchanged", action="store_true",
                    help="(upload) don't rewrite slots whose recorded content is identical")
    pl.add_argument("--server", default="http://127.0.0.1:8765",
                    help="use the running web server's BLE connection if reachable ('' = direct BLE)")

    w = sub.add_parser("serve", help="run local web UI/API")
    w.add_argument("--host", default="127.0.0.1")
    w.add_argument("--port", type=int, default=8765)
    return p


def _anim(v: str) -> int:
    v = v.strip().upper().replace("-", "_")
    return int(v) if v.isdigit() else int(P.TextAnimation[v])


async def _run(a) -> int:
    if a.cmd == "scan":
        found = await scan(a.timeout)
        for dev, rssi in found:
            print(f"{dev.name}\t{dev.address}\trssi={rssi}")
        if not found:
            print("no LED_BLE_ devices found", file=sys.stderr)
        return 0 if found else 1

    if a.cmd == "preset" and not a.name:
        from .presets import list_presets
        for m in list_presets():
            print(f"{m['name']:24} {m.get('kind', 'image'):5} {m.get('description', '')}")
        return 0

    async with SignClient(a.address or None, rotate_180=a.rotate) as sign:
        if a.cmd == "info":
            print(sign.state.device_info)
        elif a.cmd == "send-text":
            await sign.send_text(a.text, color=a.color, bg_color=a.bg, animation=_anim(a.anim),
                                 speed=a.speed, rainbow=a.rainbow)
        elif a.cmd == "image":
            await sign.send_image(Image.open(a.file))
        elif a.cmd == "test-image":
            await sign.send_image(test_pattern())
        elif a.cmd == "preset":
            from .presets import play
            await play(sign, a.name)
        elif a.cmd == "splash":
            from .branding import splash_gif
            gif, frames = splash_gif(a.text)
            await sign.send_gif(gif, frames[0])
        elif a.cmd == "project":
            from .branding import default_preset, project_frame
            name = default_preset(a.key) if a.kind == "default" else None
            if name:
                from .presets import play
                await play(sign, name)
            else:
                await sign.send_image(project_frame(a.key, "banner" if a.kind == "default" else a.kind))
        elif a.cmd == "gif":
            with open(a.file, "rb") as fh:
                await sign.send_gif(fh.read())
        elif a.cmd == "status":
            await sign.send_image(compose_status(a.title, a.subtitle, a.icon))
        elif a.cmd == "brightness":
            await sign.brightness(a.level)
        elif a.cmd == "power":
            await sign.power(a.state == "on")
        elif a.cmd == "clear":
            await sign.clear()
        acks = [n for n in sign.state.notifications]
        for n in acks:
            print(f"notify {n['hex']} ({n['kind']})")
    print("ok")
    return 0


def main(argv=None) -> int:
    a = _parser().parse_args(argv)
    logging.basicConfig(level=logging.WARNING - 10 * min(a.verbose, 2),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if a.cmd == "config":
        from . import settings as S
        if a.key and a.value is not None:
            v = a.value.lower() in ("1", "on", "true", "yes") if a.value.lower() in (
                "1", "0", "on", "off", "true", "false", "yes", "no") else a.value
            print(S.save({a.key: v}))
        else:
            print(S.load() if not a.key else S.get(a.key))
        return 0
    if a.cmd == "watch":
        from .ai_status import watch_main
        return watch_main(a)
    if a.cmd == "playlist":
        from .playlist_cli import main as pl_main
        return pl_main(a)
    if a.cmd == "serve":
        from .web import serve
        serve(a.host, a.port, a.address or None)
        return 0
    try:
        return asyncio.run(_run(a))
    except Exception as e:  # noqa: BLE001
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
