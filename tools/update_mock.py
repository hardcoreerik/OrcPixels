"""Local stand-in for the GitHub Releases API + downloads (testing the OTA updater).

    python tools/update_mock.py DIR [--port 8799] [--prerelease]

DIR holds OrcPixels-Setup-<v>.exe (+ SHA256SUMS, made if missing). Serves
  /releases            GitHub-shaped JSON (one release per installer, ETag/304 support)
  /dl/<name>           the files
Point the app at it:  ORCPIXELS_UPDATE_API=http://127.0.0.1:8799/releases
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

NOTES = "## What's new\n- **OTA updates**: Update now from the tray or Settings\n- SHA-256 verified installers\n- Bug fixes"


def releases(d: Path, port: int, pre: bool) -> list:
    exes = sorted(d.glob("OrcPixels-Setup-*.exe"))
    sums = d / "SHA256SUMS"
    sums.write_text("".join(f"{hashlib.sha256(e.read_bytes()).hexdigest()}  {e.name}\n" for e in exes))
    out = []
    for e in exes:
        v = re.match(r"OrcPixels-Setup-(.+)\.exe", e.name).group(1)
        base = f"http://127.0.0.1:{port}/dl"
        out.append({"tag_name": f"v{v}", "name": f"OrcPixels v{v}", "draft": False, "prerelease": pre and "-" in v,
                    "body": NOTES, "html_url": f"https://github.com/hardcoreerik/OrcPixels/releases/tag/v{v}",
                    "published_at": "2026-10-10T17:00:00Z",
                    "assets": [{"name": e.name, "size": e.stat().st_size, "browser_download_url": f"{base}/{e.name}"},
                               {"name": "SHA256SUMS", "size": sums.stat().st_size, "browser_download_url": f"{base}/SHA256SUMS"}]})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--port", type=int, default=8799)
    ap.add_argument("--prerelease", action="store_true")
    a = ap.parse_args()
    d = Path(a.dir)

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/releases"):
                body = json.dumps(releases(d, a.port, a.prerelease)).encode()
                etag = '"' + hashlib.sha1(body).hexdigest()[:16] + '"'
                if self.headers.get("If-None-Match") == etag:
                    self.send_response(304)
                    self.send_header("X-RateLimit-Remaining", "59")
                    self.end_headers()
                    return
                self.send_response(200)
                for k, v in (("Content-Type", "application/json"), ("ETag", etag),
                             ("X-RateLimit-Remaining", "59"), ("X-RateLimit-Reset", "0")):
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)
                return
            p = d / self.path.removeprefix("/dl/")
            if self.path.startswith("/dl/") and p.is_file() and p.parent == d:
                data = p.read_bytes()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            self.send_response(404)
            self.end_headers()

    print(f"update mock on http://127.0.0.1:{a.port}/releases serving {d}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), H).serve_forever()


if __name__ == "__main__":
    main()
