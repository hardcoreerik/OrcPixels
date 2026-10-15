"""More money screens (2026-10-14), all keyless:

  Crypto Fear & Greed Index (alternative.me /fng; daily value, attribution requested)
  Bitcoin network (mempool.space REST: recommended fees + block height; be polite, no hard limit published)
  US national debt (US Treasury Fiscal Data, debt_to_penny; updated each business day)
"""
from __future__ import annotations

from .. import categories as CAT
from .. import live_adaptive as A
from .. import live_templates as T
from ..sources import livefeeds as LF

COLOR = (60, 225, 90)


def fng_col(n):
    for lim, c in ((25, (255, 50, 40)), (45, (255, 140, 20)), (56, (240, 220, 40)), (75, (140, 245, 160))):
        if n < lim:
            return c
    return (40, 210, 90)


class FngFeed(LF.Feed):
    key, title, source, poll_s = "fear_greed", "Crypto Fear & Greed index", "alternative.me Fear & Greed (keyless)", 3 * 3600.0
    url = "https://api.alternative.me/fng/?limit=14"

    def fetch(self) -> dict:
        d = T.cached(self, self.url, 3600.0)
        rows = [{"v": int(r["value"]), "cls": A.clean(r.get("value_classification")).upper()} for r in d.get("data") or []]
        if not rows:
            raise ValueError("no index")
        return {"rows": rows}


class FearGreed(T.ValueScreen):
    key, short, feed, category, color = "fear_greed", "FEAR/GREED", FngFeed, "markets", COLOR

    def value(self, v, now):
        r = v["rows"]
        n = r[0]["v"]
        prev = r[1]["v"] if len(r) > 1 else n
        ch = n - prev
        return {"big": str(n), "label": "FEAR/GREED", "sub": r[0]["cls"], "col": fng_col(n), "gauge": n / 100,
                "right": f"{ch:+d}", "spark": [x["v"] for x in reversed(r)],
                "extras": [("YESTERDAY", f"{prev} {r[1]['cls'] if len(r) > 1 else ''}", fng_col(prev)),
                           ("WEEK AGO", str(r[7]["v"]) if len(r) > 7 else "?", fng_col(r[7]["v"]) if len(r) > 7 else None)]}


class BtcFeed(LF.Feed):
    key, title, source, poll_s = "btc_network", "Bitcoin network: fees + block", "mempool.space API (keyless)", 600.0
    url = "https://mempool.space/api/v1/fees/recommended"

    def fetch(self) -> dict:
        fees = T.cached(self, self.url, 300.0)
        h = T.cached(self, "https://mempool.space/api/blocks/tip/height", 300.0, raw=True)
        return {"fast": fees.get("fastestFee"), "half": fees.get("halfHourFee"), "hour": fees.get("hourFee"),
                "eco": fees.get("economyFee"), "height": int(str(h).strip())}


class BtcNet(T.ValueScreen):
    key, short, feed, category, color = "btc_network", "BTC FEES", BtcFeed, "markets", (255, 168, 20)

    def value(self, v, now):
        f = v["fast"] or 0
        col = (60, 225, 90) if f < 10 else (240, 220, 40) if f < 40 else (255, 50, 40)
        return {"big": str(f), "unit": "SAT", "label": "BTC FEE", "sub": f"BLOCK {v['height']:,}", "col": col,
                "right": "SAT/VB",
                "extras": [("30 MIN", f"{v['half']} SAT/VB", None), ("1 HOUR", f"{v['hour']} SAT/VB", None),
                           ("ECONOMY", f"{v['eco']} SAT/VB", None), ("BLOCK", f"{v['height']:,}", A.CREAM)]}


class DebtFeed(LF.Feed):
    key, title, source, poll_s = "us_debt", "US national debt", "US Treasury Fiscal Data debt_to_penny (keyless)", 6 * 3600.0
    url = ("https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v2/accounting/od/debt_to_penny"
           "?sort=-record_date&page[size]=30")

    def fetch(self) -> dict:
        d = T.cached(self, self.url, 3 * 3600.0)
        rows = [{"date": r["record_date"], "tot": float(r["tot_pub_debt_out_amt"]), "pub": float(r.get("debt_held_public_amt") or 0)}
                for r in d.get("data") or [] if r.get("tot_pub_debt_out_amt")]
        if not rows:
            raise ValueError("no data")
        return {"rows": rows}


class UsDebt(T.ValueScreen):
    key, short, feed, category, color = "us_debt", "US DEBT", DebtFeed, "markets", (255, 236, 190)

    def value(self, v, now):
        r = v["rows"]
        tot = r[0]["tot"]
        day = (tot - r[1]["tot"]) if len(r) > 1 else 0
        month = (tot - r[-1]["tot"]) if len(r) > 1 else 0
        return {"big": f"${tot / 1e12:.2f}T", "label": "US DEBT", "sub": f"AS OF {r[0]['date'][5:]}", "col": A.CREAM,
                "right": r[0]["date"][5:], "spark": [x["tot"] for x in reversed(r)],
                "extras": [(f"SINCE {r[1]['date'][5:]}" if len(r) > 1 else "CHANGE", f"{'+' if day >= 0 else '-'}${abs(day) / 1e9:.1f}B", (255, 120, 40) if day > 0 else (60, 225, 90)),
                           (f"{len(r)} DAYS", f"{'+' if month >= 0 else '-'}${abs(month) / 1e9:.0f}B", None),
                           ("PUBLIC", f"${r[0]['pub'] / 1e12:.2f}T", None)]}


SCREENS = [FearGreed(), BtcNet(), UsDebt()]
for _s in SCREENS:
    CAT.assign(_s.key, "markets")
