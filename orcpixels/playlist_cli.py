"""`python -m orcpixels playlist ...` — uses the running web server (which owns the BLE
connection) when reachable, otherwise connects to the sign directly."""
from __future__ import annotations

import asyncio
import json
import sys
import time
import urllib.error
import urllib.request

from . import playlist as PL


def _http(server: str, method: str, path: str, body: dict | None = None, timeout: float = 15):
    req = urllib.request.Request(server.rstrip("/") + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        try:
            detail = json.loads(detail).get("detail", detail)
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError(f"server {e.code}: {detail}") from None


def _server_up(server: str) -> bool:
    if not server:
        return False
    try:
        _http(server, "GET", "/api/playlists", timeout=3)
        return True
    except Exception:  # noqa: BLE001
        return False


def _fmt_s(s) -> str:
    return "?" if s is None else (f"{s:.0f}s" if s < 90 else f"{s / 60:.1f}min")


def _print_progress(p: dict):
    bar = "#" * int(p["pct"] / 5) + "-" * (20 - int(p["pct"] / 5))
    sys.stdout.write(f"\r[{bar}] {p['pct']:5.1f}% item {p['item']}/{p['items']} {p['current'] or ''}"
                     f" {p['bytes_sent']}/{p['bytes_total']} B eta {_fmt_s(p['eta_s'])}     ")
    sys.stdout.flush()


def _show_slots(rec: dict):
    act = rec.get("active")
    print("play order:", f"{act['playlist']} -> slots {act['slots']} (set {act.get('played_at') or act['set_at']})"
          if act else "(none set by OrcPixels)")
    for k in sorted(rec["slots"], key=int):
        e = rec["slots"][k]
        print(f"  slot {int(k):3}: {e['preset']:28} {e['kind']:4} {e['bytes']:6} B  {e['written']}  ack {e['ack']}")
    print(f"  measured upload rate: {rec.get('rate_bps')} B/s")


def main(a) -> int:
    try:
        return _main(a)
    except Exception as e:  # noqa: BLE001
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return 2


def _main(a) -> int:
    if a.action == "list":
        s = PL.summary()
        for name, pl in s["playlists"].items():
            print(f"{name:20} {len(pl['items'])} item(s) slots {pl['slots'][0]}..{pl['slots'][-1]}"
                  f"  ~{pl['approx_bytes']} B, upload ~{_fmt_s(pl['approx_upload_s'])}")
        if not s["playlists"]:
            print("(no playlists; create one with 'playlist save <name> <preset> ...')")
        return 0
    if a.action == "slots":
        _show_slots(PL.slot_record())
        return 0
    if a.action == "show":
        if not a.name:
            raise SystemExit("usage: playlist show <name>")
        pl = PL.summary()["playlists"].get(a.name) or PL.get(a.name)
        for slot, it in zip(pl["slots"], pl["items"]):
            d = f"{it['duration']:g}s" if it.get("duration") else "device default"
            print(f"  slot {slot:3}: {it['preset']:28} duration {d}")
        print(f"  ~{pl['approx_bytes']} B, upload ~{_fmt_s(pl['approx_upload_s'])}")
        return 0
    if a.action == "save":
        if not a.name or not a.presets:
            raise SystemExit("usage: playlist save <name> <preset>[:seconds] ...")
        items = []
        for p in a.presets:
            n, _, d = p.partition(":")
            items.append({"preset": n, "duration": float(d) if d else None})
        pl = PL.save(a.name, items, a.base_slot)
        print(f"saved {a.name}: {[i['preset'] for i in pl['items']]} base slot {pl['base_slot']}")
        return 0

    # ---- device actions
    if _server_up(a.server):
        if a.action == "upload":
            if not a.name:
                raise SystemExit("usage: playlist upload <name>")
            q = "?skip_unchanged=true" if a.skip_unchanged else ""
            p = _http(a.server, "POST", f"/api/playlists/{a.name}/upload{q}")
            print(f"upload started via web server ({a.server})")
            while True:
                time.sleep(1.0)
                p = _http(a.server, "GET", "/api/playlists/upload")
                _print_progress(p)
                if not p["running"]:
                    break
            print()
            for line in p["log"]:
                print(" ", line)
            return 0 if p["phase"] == "done" else 1
        r = _http(a.server, "POST", "/api/playlists/play" + (f"?name={a.name}" if a.name else ""))
        print(f"playing {r['playlist']} slots {r['slots']} (ack {r['ack']})")
        return 0

    from .ble_sign import DEFAULT_ADDRESS, SignClient

    async def direct():
        async with SignClient(a.address or DEFAULT_ADDRESS, rotate_180=a.rotate) as sign:
            if a.action == "upload":
                if not a.name:
                    raise SystemExit("usage: playlist upload <name>")
                task = asyncio.create_task(PL.upload(sign, a.name, skip_unchanged=a.skip_unchanged))
                while not task.done():
                    await asyncio.sleep(1.0)
                    _print_progress(PL.PROGRESS.as_dict())
                print()
                r = task.result()
                for line in PL.PROGRESS.log:
                    print(" ", line)
                print(f"done in {r['elapsed_s']} s ({r['rate_bps']} B/s)")
            else:
                r = await PL.play(sign, a.name)
                print(f"playing {r['playlist']} slots {r['slots']} (ack {r['ack']})")
        return 0

    print("(web server not reachable; connecting to the sign directly)")
    return asyncio.run(direct())
