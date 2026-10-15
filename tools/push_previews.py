"""Push API previews: python tools/push_previews.py [out.png]   (sheet at every test size)
                   python tools/push_previews.py --golden      (rewrite tests/golden/push.json)"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw  # noqa: E402

from orcpixels import layout as L  # noqa: E402
from orcpixels import push as PU  # noqa: E402

SIZES = [(64, 16), (64, 64), (16, 16), (64, 32), (256, 16), (96, 24)]


def samples():
    t = [0.0]
    b = PU.Board(clock=lambda: t[0])
    s1 = b.set_status("rsa", "RSA", "Counting lines of code", 42, "repo OrcPixels", 30)
    s2 = b.set_status("rsa2", "RSA", "Cloning repository", None, "", 30)
    t[0] = 37.0
    s3 = b.set_status("rsa3", "RSA", "Writing report", 100, "", 30)
    f = b.set_flash("rsa4", "RSA", "96k LOC", "OrcPixels done", 5)
    # /api/rsa/* shapes (what rsa_orcpixels.py sends), mapped as the routes do
    r1 = b.set_status("rsa", "RSA 3/7", "Counting LOC", 57, "", 60)
    r2 = b.set_flash("rsa5", "RSA", "7.4k", "LOC", 5, True, "DONE")
    r3 = b.set_flash("rsa6", "RSA", "FAIL", "git clone", 5, False, "FAIL")
    return [("status 42%", ("status", s1)), ("status (no pct)", ("status", s2)),
            ("status 100%", ("status", s3)), ("flash", ("flash", f)),
            ("rsa status", ("status", r1)), ("rsa flash DONE", ("flash", r2)), ("rsa flash FAIL", ("flash", r3))]


def sheet(out):
    rows = []
    for label, cur in samples():
        tiles = []
        for w, h in SIZES:
            sc = 4 if max(w, h) <= 96 else 2
            frame = PU.render(cur, (w, h), k=2, now=37.0 + 42)
            tiles.append(L.led_preview(frame, sc))
        rows.append((label, tiles))
    pad = 10
    W = max(sum(t.width for t in tiles) + pad * (len(tiles) + 1) for _, tiles in rows)
    H = sum(max(t.height for t in tiles) + 26 for _, tiles in rows) + pad
    img = Image.new("RGB", (W, H), (14, 15, 18))
    d = ImageDraw.Draw(img)
    y = pad
    for label, tiles in rows:
        d.text((pad, y), label + "   (" + ", ".join(f"{w}x{h}" for w, h in SIZES) + ")", fill=(200, 200, 200))
        y += 16
        x = pad
        for t in tiles:
            img.paste(t, (x, y))
            x += t.width + pad
        y += max(t.height for t in tiles) + 10
    img.save(out)
    return out


GOLD = Path(__file__).resolve().parents[1] / "tests" / "golden" / "push.json"


def golden() -> dict:
    """Native 64x16 / 64x64 golden hashes of the sample screens (first 6 frames)."""
    import hashlib
    out = {}
    for label, cur in samples():
        for size in ((64, 16), (64, 64)):
            h = hashlib.sha256()
            for k in range(6):
                h.update(PU.render(cur, size, k=k, now=79.0).tobytes())
            out[f"{label}@{size[0]}x{size[1]}"] = h.hexdigest()[:16]
    return out


if __name__ == "__main__":
    if "--golden" in sys.argv:                 # deliberate update of tests/golden/push.json
        import json
        GOLD.write_text(json.dumps(golden(), indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(GOLD)
    else:
        print(sheet(sys.argv[1] if len(sys.argv) > 1 else "push_api_previews.png"))
