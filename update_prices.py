"""Daily price pipeline for the market terminal.

Downloads ~13 months of daily closes for every asset in assets.csv and writes:
  data/prices.json  - {"updated", "assets": {KEY: {"t": [dates], "c": [closes]}}}
  data/prices.csv   - wide table, one column per asset (for analysis in pandas/Excel)
  data/status.json  - which assets updated, last date per asset, what failed

Sources per asset (assets.csv columns): `treasury` (US Treasury daily par yield
curve, same-day), `fred` (FRED series, ~1 day lag), `yahoo` (yfinance; several
tickers may be given as A|B, the first one with data wins). When an asset has
several sources, the one with the freshest last date is used.

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


def treasury_curve() -> pd.DataFrame | None:
    """US Treasury daily par yield curve for the current and previous year."""
    frames = []
    year = datetime.now(timezone.utc).year
    for y in (year - 1, year):
        url = (f"https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
               f"daily-treasury-rates.csv/{y}/all?type=daily_treasury_yield_curve"
               f"&field_tdr_date_value={y}&page&_format=csv")
        try:
            r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            df = pd.read_csv(io.StringIO(r.text))
            df["Date"] = pd.to_datetime(df["Date"], format="%m/%d/%Y")
            frames.append(df.set_index("Date"))
        except Exception as e:  # noqa: BLE001
            print(f"  Treasury {y} failed: {e}")
    return pd.concat(frames).sort_index() if frames else None


def main() -> None:
    assets = load_assets()
    first = [a["yahoo"].split("|")[0] for a in assets if a["yahoo"]]
    print(f"Downloading {len(first)} Yahoo tickers…")
    got = yahoo_bulk(first)

    curve = treasury_curve() if any(a.get("treasury") for a in assets) else None

    result: dict[str, pd.Series] = {}
    sources: dict[str, str] = {}
    for a in assets:
        k = a["key"]
        cands: list[tuple[str, pd.Series]] = []  # in priority order for ties
        col = a.get("treasury")
        if col and curve is not None and col in curve:
            t = clean(curve[col])
            if len(t) > 5:
                cands.append(("treasury", t))
        if a["fred"]:
            f = fred(a["fred"])
            if f is not None:
                cands.append((f"fred:{a['fred']}", f))
        for tk in [x for x in a["yahoo"].split("|") if x]:
            s = got.get(tk)
            if s is None:
                print(f"Retrying {tk} individually…")
                s = yahoo_single(tk)
            if s is not None:
                cands.append((f"yahoo:{tk}", s))
                break
        if cands:
            best = max(range(len(cands)), key=lambda i: (cands[i][1].index.max(), -i))
            sources[k], result[k] = cands[best]

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
        "no_source": [a["key"] for a in assets if not (a["yahoo"] or a["fred"] or a.get("treasury"))],
        "last_date": {k: s.index.max().strftime("%Y-%m-%d") for k, s in result.items()},
        "source": sources,
    }
    (OUT / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
    meta = [{k: a[k] for k in ("key", "name", "group", "base", "kind", "decimals", "desc")} for a in assets]
    (OUT / "assets.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    print(f"OK {len(result)}/{len(assets)}; missing: {missing}")


if __name__ == "__main__":
    main()
