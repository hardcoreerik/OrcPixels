"""One-time move of a slot record + playlists that the pre-2026-10-08 server wrote for the
64x64 into the shared files (config/device_slots.json, config/playlists.json).

Run with the server STOPPED. Dry run by default; --apply writes (originals kept as *.bak):

    python tools/migrate_slot_record.py --to matrix64 --playlist 64x64 [--apply]

- config/device_slots.json  -> config/displays/<id>/device_slots.json (tagged with that
  sign's address); the primary gets an empty record tagged with its own address, so its
  30 s rule plays its own Default/fallback, never the 64x64's slot order.
- the named playlists move from config/playlists.json to config/displays/<id>/playlists.json.
Nothing is sent to any sign.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from orcpixels import displays as D  # noqa: E402

CFG = ROOT / "config"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--to", required=True, help="display id the shared record belongs to")
    ap.add_argument("--playlist", action="append", default=[], help="playlist name to move (repeatable)")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    dst = D.get(a.to)
    prim = D.get(D.primary_id())
    if dst["id"] == prim["id"]:
        sys.exit("target must be a secondary display")
    slots, pls = CFG / "device_slots.json", CFG / "playlists.json"
    ddir = CFG / "displays" / dst["id"]
    rec = json.loads(slots.read_text(encoding="utf-8")) if slots.exists() else {}
    if rec.get("address") and rec["address"].upper() != dst["address"].upper():
        sys.exit(f"record is already tagged for {rec['address']}: nothing to migrate")
    data = json.loads(pls.read_text(encoding="utf-8")) if pls.exists() else {"playlists": {}}
    moving = {n: data["playlists"][n] for n in a.playlist if n in data.get("playlists", {})}
    missing = [n for n in a.playlist if n not in moving]
    act = (rec.get("active") or {}).get("playlist")
    print(f"slot record: {len(rec.get('slots', {}))} slot(s), active playlist {act!r} -> {ddir / 'device_slots.json'}")
    print(f"playlists to move -> {ddir / 'playlists.json'}: {sorted(moving)}" + (f" (not found: {missing})" if missing else ""))
    if not a.apply:
        print("dry run: add --apply to write (server must be stopped)")
        return
    ddir.mkdir(parents=True, exist_ok=True)
    for f in (slots, pls):
        if f.exists():
            shutil.copy2(f, f.with_suffix(f.suffix + ".bak"))
    tgt_slots = ddir / "device_slots.json"
    if tgt_slots.exists():
        shutil.copy2(tgt_slots, tgt_slots.with_suffix(".json.bak"))
    tgt_slots.write_text(json.dumps({**rec, "address": dst["address"].upper()}, indent=2) + "\n", encoding="utf-8")
    slots.write_text(json.dumps({"slots": {}, "active": None, "rate_bps": rec.get("rate_bps", 1100.0),
                                 "address": prim["address"].upper()}, indent=2) + "\n", encoding="utf-8")
    tp = ddir / "playlists.json"
    tdata = json.loads(tp.read_text(encoding="utf-8")) if tp.exists() else {"playlists": {}}
    if not tp.exists() and "Default" in data.get("playlists", {}):
        tdata["playlists"]["Default"] = dict(data["playlists"]["Default"])   # its own copy, as the server would seed
    tdata.setdefault("playlists", {}).update(moving)
    tp.write_text(json.dumps(tdata, indent=2) + "\n", encoding="utf-8")
    for n in moving:
        data["playlists"].pop(n, None)
    pls.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print("done (backups: *.bak)")


if __name__ == "__main__":
    main()
