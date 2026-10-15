"""Check / publish the declarative free-source catalog.

  python tools/build_catalog.py                       # validate orcpixels/data/free_sources.json
  python tools/build_catalog.py --sync                # also copy it to catalog/free_sources.json
  python tools/build_catalog.py --fork path/to/public-apis/README.md
                                                      # report which catalog providers are listed in
                                                      # the public-apis fork (hardcoreerik/public-apis)

catalog/free_sources.json is the file "Get more free sources" downloads from the public
OrcPixels repo (live_catalog.CATALOG_URL): publishing new sources = editing the bundled
file, running --sync, and pushing to the public repo. No app release needed.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sync", action="store_true")
    ap.add_argument("--fork", help="public-apis README.md to cross-reference")
    a = ap.parse_args()
    from orcpixels import live_catalog as CATL
    from orcpixels import livedata_screens as SC
    doc = json.loads(CATL.BUNDLED.read_text(encoding="utf-8"))
    builtin = CATL.builtin_keys(SC.SCREENS)
    clean, errs = CATL.validate(doc, builtin)
    print(f"catalog v{clean['version']}: {len(clean['sources'])} valid sources, {len(errs)} problems")
    for e in errs:
        print("  !", e)
    if a.fork:
        text = Path(a.fork).read_text(encoding="utf-8")
        hosts = set(re.findall(r"\]\((https?://[^)]+)\)", text))
        for s in clean["sources"]:
            host = re.sub(r"^https://(api\.|www\.)?", "", s["url"]).split("/")[0]
            hit = any(host.split(".")[-2] in h for h in hosts)
            print(f"  {'listed ' if hit else 'NOT listed'}  {s['id']:16} {host}")
    out = ROOT / "catalog" / "free_sources.json"
    if a.sync:
        out.parent.mkdir(exist_ok=True)
        out.write_text(CATL.BUNDLED.read_text(encoding="utf-8"), encoding="utf-8")
        print("synced", out)
    elif out.exists() and json.loads(out.read_text(encoding="utf-8")) != doc:
        print("catalog/free_sources.json is out of date: run with --sync")
        return 1
    return 1 if errs else 0


if __name__ == "__main__":
    raise SystemExit(main())
