"""Daily price pipeline for the market terminal.

Downloads ~13 months of daily closes for every asset in assets.csv
(Yahoo Finance via yfinance, FRED as fallback for US yields) and writes:
  data/prices.json  - {"updated", "assets": {KEY: {"t": [dates], "c": [closes]}}}
  data/prices.csv   - wide table, one column per asset (for analysis in pandas/Excel)
  data/status.json  - which assets updated, last date per asset, what failed

The full window is re-downloaded on every run, so a missed or failed run
is repaired by the next one. Run locally:  python update_prices.py
"""
from __future__ import annotations

import csv
import io
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data"
PERIOD = "13mo"
KEEP_DAYS = 400


def load_assets() -> list[dict]:
    with open(ROOT / "assets.csv", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def clean(series: pd.Series) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce").dropna()
    s = s[~s.index.duplicated(keep="last")].sort_index()
    s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
    return s[s.index >= s.index.max() - pd.Timedelta(days=KEEP_DAYS)] if len(s) else s


def yahoo_bulk(tickers: list[str]) -> dict[str, pd.Series]:
    out: dict[str, pd.Series] = {}
    if not tickers:
        return out
    df = yf.download(tickers, period=PERIOD, interval="1d", auto_adjust=False,
                     progress=False, threads=True, group_by="column")
    close = df["Close"] if "Close" in df else pd.DataFrame()
    if isinstance(close, pd.Series):
        close = close.to_frame(tickers[0])
    for t in tickers:
        if t in close:
            s = clean(close[t])
            if len(s) > 5:
                out[t] = s
    return out


def yahoo_single(ticker: str, tries: int = 3) -> pd.Series | None:
    for i in range(tries):
        try:
            h = yf.Ticker(ticker).history(period=PERIOD, interval="1d", auto_adjust=False)
            s = clean(h["Close"]) if "Close" in h else None
            if s is not None and len(s) > 5:
                return s
        except Exception as e:  # noqa: BLE001
            print(f"  {ticker}: attempt {i + 1} failed: {e}")
        time.sleep(2 * (i + 1))
    return None


def fred(series_id: str) -> pd.Series | None:
    try:
        r = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv",
                         params={"id": series_id}, timeout=30)
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
        df.columns = ["date", "v"]
        s = pd.Series(pd.to_numeric(df["v"], errors="coerce").values,
                      index=pd.to_datetime(df["date"]))
        s = clean(s)
        return s if len(s) > 5 else None
    except Exception as e:  # noqa: BLE001
        print(f"  FRED {series_id} failed: {e}")
        return None


def main() -> None:
    assets = load_assets()
    yahoo = [a["yahoo"] for a in assets if a["yahoo"]]
    print(f"Downloading {len(yahoo)} Yahoo tickers…")
    got = yahoo_bulk(yahoo)

    for t in [t for t in yahoo if t not in got]:
        print(f"Retrying {t} individually…")
        s = yahoo_single(t)
        if s is not None:
            got[t] = s

    result: dict[str, pd.Series] = {}
    sources: dict[str, str] = {}
    for a in assets:
        k, y, fr = a["key"], a["yahoo"], a["fred"]
        s = got.get(y) if y else None
        src = "yahoo"
        # Yahoo yield futures can lag or gap: prefer whichever source has the fresher last date.
        if fr:
            f = fred(fr)
            if f is not None and (s is None or f.index.max() > s.index.max()):
                s, src = f, "fred"
        if s is not None:
            result[k], sources[k] = s, src

    OUT.mkdir(exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {"updated": now, "assets": {}}
    for k, s in result.items():
        payload["assets"][k] = {
            "t": [d.strftime("%Y-%m-%d") for d in s.index],
            "c": [round(float(v), 6) for v in s.values],
        }
    (OUT / "prices.json").write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")

    wide = pd.DataFrame(result).sort_index()
    wide.index.name = "date"
    wide.to_csv(OUT / "prices.csv", float_format="%.6g")

    missing = [a["key"] for a in assets if a["key"] not in result]
    status = {
        "updated": now,
        "ok": len(result),
        "total": len(assets),
        "missing": missing,
        "no_source": [a["key"] for a in assets if not a["yahoo"] and not a["fred"]],
        "last_date": {k: s.index.max().strftime("%Y-%m-%d") for k, s in result.items()},
        "source": sources,
    }
    (OUT / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
    meta = [{k: a[k] for k in ("key", "name", "group", "base", "kind", "decimals", "desc")} for a in assets]
    (OUT / "assets.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    print(f"OK {len(result)}/{len(assets)}; missing: {missing}")


if __name__ == "__main__":
    main()
