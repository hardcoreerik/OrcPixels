"""Build the curated 64x64 preset set: presets64/<name>.{gif,png,json} (2026-10-08).

Same names, families and titles as presets/ (the 64x16 strip), each with its own 64x64
layout (tools/p64_*.py). The web UI shows this folder when the 64x64 matrix is selected
(presets.dir_for). Every animated preset is checked with orcpixels.loopmetric.

    python tools/make_presets64.py                 # build all
    python tools/make_presets64.py p_rf_iq ...     # build selected
"""
from __future__ import annotations

import datetime as dt
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import p64lib as L  # noqa: E402
import p64_orcsdr  # noqa: E402,F401
import p64_personal  # noqa: E402,F401
import p64_orco  # noqa: E402,F401
from orcpixels.loopmetric import loop_metric_frames  # noqa: E402
from orcpixels.render import encode_gif  # noqa: E402

OUT = ROOT / "presets64"
p64_orcsdr.setup()
p64_personal.setup()
p64_orco.setup()


def frames_of(spec):
    n = spec["n"] or 1
    return [spec["fn"](t, n).convert("RGB") for t in range(n)]


def build(name: str) -> dict:
    spec = L.REGISTRY[name]
    meta16 = L.load_meta(name)
    frames = frames_of(spec)
    assert all(f.size == (64, 64) for f in frames), name
    OUT.mkdir(exist_ok=True)
    m = None
    if len(frames) > 1:
        m = loop_metric_frames(frames)
        data = encode_gif(frames, spec["ms"])
        ext, kind = "gif", "gif"
    else:
        buf = io.BytesIO()
        frames[0].save(buf, format="PNG", optimize=True)
        data, ext, kind = buf.getvalue(), "png", "image"
    (OUT / f"{name}.{ext}").write_bytes(data)
    prev = f"{name}_preview_6x.png"
    frames[0].resize((384, 384), 0).save(OUT / prev)
    meta = {k: v for k, v in meta16.items() if k not in ("file", "preview", "bytes", "frames", "frame_ms", "loop",
                                                          "generator", "created", "kind")}
    meta.update({"name": name, "kind": kind, "file": f"{name}.{ext}", "preview": prev, "size": "64x64",
                 "frames": len(frames), "frame_ms": spec["ms"] if len(frames) > 1 else None, "bytes": len(data),
                 "loop": (("seamless" if m["ok"] else "JUMP") if m else None),
                 "layout64": spec["note"], "generator": "tools/make_presets64.py",
                 "created": dt.datetime.now().isoformat(timespec="seconds")})
    (OUT / f"{name}.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return meta


def main(argv):
    names = argv or sorted(L.REGISTRY)
    missing = sorted({p.stem for p in (ROOT / "presets").glob("*.json")} - set(L.REGISTRY))
    bad = []
    for nm in names:
        meta = build(nm)
        flag = "" if meta["loop"] in (None, "seamless") else "  <-- " + meta["loop"]
        print(f"{nm:30s} {meta['kind']:5s} {meta['frames']:3d}f {meta['bytes'] / 1024:6.1f} KB{flag}")
        if flag:
            bad.append(nm)
    if missing:
        print("NOT CURATED YET (falls back to the 64x16 file):", ", ".join(missing))
    if bad:
        print("LOOP JUMPS:", bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
