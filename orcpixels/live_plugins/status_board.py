"""Service status board (2026-10-14): public Atlassian Statuspage summaries, keyless,
polled every 5 min. Default services: GitHub, Cloudflare, Discord, OpenAI, Reddit, Claude."""
from __future__ import annotations

from .. import categories as CAT
from .. import live_adaptive as A
from .. import live_templates as T
from ..sources import livefeeds as LF

COLOR = (40, 210, 90)
SERVICES = (("GITHUB", "https://www.githubstatus.com"), ("CLOUDFLARE", "https://www.cloudflarestatus.com"),
            ("DISCORD", "https://discordstatus.com"), ("OPENAI", "https://status.openai.com"),
            ("REDDIT", "https://www.redditstatus.com"), ("CLAUDE", "https://status.claude.com"))
IND = {"none": ("OK", (60, 225, 90)), "minor": ("MINOR", (240, 220, 40)), "major": ("MAJOR", (255, 120, 40)),
       "critical": ("DOWN", (255, 50, 40)), "maintenance": ("MAINT", (120, 200, 255))}


class StatusFeed(LF.Feed):
    key, title, source, poll_s = "status_board", "Service status board", "Statuspage public status.json (keyless)", 300.0
    url = "https://<service>/api/v2/status.json"

    def fetch(self) -> dict:
        rows, ok = [], 0
        for name, base in SERVICES:
            try:
                d = T.cached(self, base + "/api/v2/status.json", 240.0)
                ind = (d.get("status") or {}).get("indicator") or "none"
                desc = A.clean((d.get("status") or {}).get("description")).upper()
                ok += 1
            except Exception:  # noqa: BLE001
                ind, desc = "?", "NO ANSWER"
            rows.append({"name": name, "ind": ind, "desc": desc})
        if not ok:
            raise OSError("no status page answered")
        rows.sort(key=lambda r: (r["ind"] == "none", r["name"]))
        return {"rows": rows}


class StatusBoard(T.ValueScreen):
    key, short, feed, category, color = "status_board", "STATUS", StatusFeed, "apps", COLOR

    def value(self, v, now):
        bad = [r for r in v["rows"] if r["ind"] != "none"]
        if not bad:
            big, col, sub = "ALL OK", (60, 225, 90), f"{len(v['rows'])} SERVICES"
        else:
            big, col = f"{len(bad)} ISSUE" + ("S" if len(bad) > 1 else ""), IND.get(bad[0]["ind"], ("?", A.GREY))[1]
            sub = " ".join(r["name"] for r in bad)
        return {"big": big, "label": "STATUS", "sub": sub, "col": col, "right": f"{len(v['rows']) - len(bad)}/{len(v['rows'])}",
                "extras": [(r["name"], IND.get(r["ind"], ("?", A.GREY))[0], IND.get(r["ind"], ("?", A.GREY))[1]) for r in v["rows"]]}


SCREENS = [StatusBoard()]
CAT.assign("status_board", "apps")
