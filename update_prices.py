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


CACHE = ROOT / "data" / "cache"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/124 Safari/537.36"}


def nbk_usd() -> pd.Series | None:
    """Official USD/KZT of the National Bank of Kazakhstan, one XML request per day.

    History is cached in data/cache/nbk_usd.csv so a normal run fetches only the
    last few days; the first run backfills the whole window.
    """
    import xml.etree.ElementTree as ET
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / "nbk_usd.csv"
    have: dict[str, float] = {}
    if path.exists():
        have = {r["date"]: float(r["usd"]) for r in csv.DictReader(open(path, encoding="utf-8"))}
    today = pd.Timestamp.now(tz="Asia/Almaty").normalize().tz_localize(None)
    start = today - pd.Timedelta(days=KEEP_DAYS)
    days = [d for d in pd.date_range(start, today + pd.Timedelta(days=1)) if d.weekday() < 5]
    recent = set(d.strftime("%Y-%m-%d") for d in days[-4:])  # refresh the last few days every run
    todo = [d for d in days if d.strftime("%Y-%m-%d") not in have or d.strftime("%Y-%m-%d") in recent]
    print(f"NBK: fetching {len(todo)} days")
    for d in todo:
        try:
            r = requests.get("https://nationalbank.kz/rss/get_rates.cfm",
                             params={"fdate": d.strftime("%d.%m.%Y")}, timeout=20, headers=UA)
            r.raise_for_status()
            root = ET.fromstring(r.content)
            if root.findtext("date") != d.strftime("%d.%m.%Y"):
                continue  # not published yet
            for it in root.iter("item"):
                if it.findtext("title") == "USD":
                    have[d.strftime("%Y-%m-%d")] = float(it.findtext("description"))
        except Exception as e:  # noqa: BLE001
            print(f"  NBK {d.date()} failed: {e}")
        time.sleep(0.15)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "usd"])
        for k in sorted(have):
            w.writerow([k, have[k]])
    if not have:
        return None
    return clean(pd.Series(have, dtype=float).rename(index=pd.Timestamp))


def bundesbank_2y() -> pd.Series | None:
    """2-year Bund yield from the Bundesbank term-structure (Svensson) series."""
    start = (pd.Timestamp.today() - pd.Timedelta(days=KEEP_DAYS + 10)).strftime("%Y-%m-%d")
    url = ("https://api.statistiken.bundesbank.de/rest/data/BBSIS/"
           "D.I.ZST.ZI.EUR.S1311.B.A604.R02XX.R.A.A._Z._Z.A")
    try:
        r = requests.get(url, params={"format": "csv", "startPeriod": start}, timeout=30, headers=UA)
        r.raise_for_status()
        vals = {}
        for line in r.text.splitlines():
            parts = line.split(";")
            if len(parts) >= 2 and len(parts[0]) == 10 and parts[0][4] == "-" and parts[1] not in (".", ""):
                vals[pd.Timestamp(parts[0])] = float(parts[1].replace(",", "."))
        s = clean(pd.Series(vals, dtype=float))
        return s if len(s) > 5 else None
    except Exception as e:  # noqa: BLE001
        print(f"  Bundesbank failed: {e}")
        return None


def nbk_base() -> pd.Series | None:
    """NBK base rate as a business-day step series from manual/nbk_base_rate.csv."""
    rows = list(csv.DictReader(open(ROOT / "manual" / "nbk_base_rate.csv", encoding="utf-8")))
    if not rows:
        return None
    steps = pd.Series({pd.Timestamp(r["effective_date"]): float(r["rate"]) for r in rows}).sort_index()
    today = pd.Timestamp.now(tz="Asia/Almaty").normalize().tz_localize(None)
    idx = pd.bdate_range(today - pd.Timedelta(days=KEEP_DAYS), today)
    s = steps.reindex(steps.index.union(idx)).ffill().reindex(idx).dropna()
    return clean(s)


SPECIAL = {"nbk_usd": nbk_usd, "bundesbank_2y": bundesbank_2y, "nbk_base": nbk_base}


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
        sp = a.get("special")
        if sp in SPECIAL:
            x = SPECIAL[sp]()
            if x is not None:  # a dedicated official source always wins
                sources[k], result[k] = sp, x
                continue
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
        "no_source": [a["key"] for a in assets if not (a["yahoo"] or a["fred"] or a.get("treasury") or a.get("special"))],
        "last_date": {k: s.index.max().strftime("%Y-%m-%d") for k, s in result.items()},
        "source": sources,
    }
    (OUT / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
    meta = [{k: a[k] for k in ("key", "name", "group", "base", "kind", "decimals", "desc")} for a in assets]
    (OUT / "assets.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    print(f"OK {len(result)}/{len(assets)}; missing: {missing}")


if __name__ == "__main__":
    main()
