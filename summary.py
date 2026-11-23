"""Deterministic numbers for the morning brief.

Reads data/prices.json + data/assets.json (local paths or the repo's raw URLs)
and prints JSON: for every asset the last close, 1-day change (% or bp for
yields), its size in sigmas, 1W/1M/YTD changes, plus the list of >=2 sigma moves.

  python summary.py                       # uses ./data
  python summary.py --raw                 # downloads from GitHub
  python summary.py --asof 2026-11-13     # numbers as of a past session
"""
from __future__ import annotations

import json
import math
import sys
import urllib.request
from datetime import date, timedelta
from pathlib import Path

RAW = "https://raw.githubusercontent.com/Aldos24/market-terminal/main/data/"


def load(name: str, raw: bool):
    if raw:
        with urllib.request.urlopen(RAW + name, timeout=30) as r:
            return json.load(r)
    return json.loads((Path(__file__).resolve().parent / "data" / name).read_text(encoding="utf-8"))


def last_le(ts: list[str], d: str) -> int:
    lo, hi, r = 0, len(ts) - 1, -1
    while lo <= hi:
        m = (lo + hi) // 2
        if ts[m] <= d:
            r, lo = m, m + 1
        else:
            hi = m - 1
    return r


def metrics(t: list[str], c: list[float], yld: bool) -> dict | None:
    if len(c) < 3:
        return None
    ch = (lambda a, b: (a - b) * 100) if yld else (lambda a, b: (a / b - 1) * 100)
    i = len(c) - 1
    d1 = ch(c[i], c[i - 1])
    hist = [ch(c[j], c[j - 1]) for j in range(max(1, i - 20), i)]
    sd = None
    if len(hist) > 5:
        mu = sum(hist) / len(hist)
        sd = math.sqrt(sum((x - mu) ** 2 for x in hist) / (len(hist) - 1))

    def back(days: int):
        d = (date.fromisoformat(t[i]) - timedelta(days=days)).isoformat()
        j = last_le(t, d)
        return round(ch(c[i], c[j]), 2) if 0 <= j < i else None

    yj = last_le(t, f"{int(t[i][:4]) - 1}-12-31")
    return {
        "date": t[i], "last": c[i], "d1": round(d1, 2),
        "sigma": round(d1 / sd, 1) if sd else None,
        "w1": back(7), "m1": back(30),
        "ytd": round(ch(c[i], c[yj]), 2) if 0 <= yj < i else None,
        "unit": "bp" if yld else "%",
    }


def main() -> None:
    raw = "--raw" in sys.argv
    asof = sys.argv[sys.argv.index("--asof") + 1] if "--asof" in sys.argv else None
    prices, meta = load("prices.json", raw), load("assets.json", raw)
    out = {"prices_updated": prices.get("updated"), "asof": asof, "assets": {}, "unusual": []}
    for a in meta:
        s = prices["assets"].get(a["key"])
        if s and asof:
            n = last_le(s["t"], asof) + 1
            s = {"t": s["t"][:n], "c": s["c"][:n]}
        m = metrics(s["t"], s["c"], a["kind"] == "yield") if s else None
        if m:
            m["name"], m["base"] = a["name"], a["base"] == "1"
            out["assets"][a["key"]] = m
            if m["sigma"] is not None and abs(m["sigma"]) >= 2:
                out["unusual"].append({"key": a["key"], "name": a["name"], "d1": m["d1"],
                                       "unit": m["unit"], "sigma": m["sigma"], "date": m["date"]})
    out["unusual"].sort(key=lambda x: -abs(x["sigma"]))
    out["missing"] = [a["key"] for a in meta if a["key"] not in out["assets"]]
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
