"""Fundamentals, earnings calendar and company news for the market terminal.

Sources (all free):
  - SEC EDGAR (no key): quarterly revenue / net income / diluted EPS from XBRL
    company facts, and links to the latest 10-K / 10-Q / 8-K filings.
  - Finnhub (FINNHUB_KEY): earnings calendar with consensus, key metrics, company news.
  - FMP (FMP_KEY): quarterly income statement (fallback for non-SEC filers), company profile.
Keys come from environment variables (GitHub Actions secrets). A missing key
just skips that source. Writes data/fundamentals.json.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "fundamentals.json"
FINNHUB = os.environ.get("FINNHUB_KEY", "").strip()
FMP = os.environ.get("FMP_KEY", "").strip()
SEC_UA = {"User-Agent": "market-terminal research (github.com/Aldos24/market-terminal) aldos24@users.noreply.github.com"}

# terminal key -> exchange ticker (stocks only)
STOCKS = {
    "NVDA": "NVDA", "AVGO": "AVGO", "AMD": "AMD", "TSM": "TSM", "ASML": "ASML", "MU": "MU",
    "MSFT": "MSFT", "GOOGL": "GOOGL", "AMZN": "AMZN", "META": "META", "ORCL": "ORCL", "AAPL": "AAPL",
    "ANET": "ANET", "ETN": "ETN", "VRT": "VRT", "CEG": "CEG", "NEE": "NEE", "CRWV": "CRWV",
    "PLTR": "PLTR", "KSPI": "KSPI", "FRHC": "FRHC", "MLI": "MLI", "LII": "LII", "WTS": "WTS",
}
REV_TAGS = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"]


def get(url: str, **kw):
    for i in range(3):
        try:
            r = requests.get(url, timeout=30, **kw)
            if r.status_code == 429:
                time.sleep(5 * (i + 1))
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            if i == 2:
                print(f"  {url.split('?')[0][-70:]}: {e}")
            time.sleep(1.5)
    return None


# ---------------- SEC EDGAR ----------------
def sec_ciks() -> dict[str, int]:
    j = get("https://www.sec.gov/files/company_tickers.json", headers=SEC_UA) or {}
    return {v["ticker"].upper(): int(v["cik_str"]) for v in j.values()}


def quarterly(facts: dict, tags: list[str], unit: str) -> list[dict]:
    g = facts.get("facts", {}).get("us-gaap", {})
    for tag in tags:
        items = g.get(tag, {}).get("units", {}).get(unit, [])
        q = {}
        for it in items:
            fr = it.get("frame", "")
            if re.fullmatch(r"CY\d{4}Q\d", fr):  # single-quarter values only
                q[fr] = {"period": fr[2:], "end": it["end"], "val": it["val"]}
        if q:
            return [q[k] for k in sorted(q)][-8:]
    return []


def sec_block(cik: int) -> dict:
    out: dict = {}
    facts = get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json", headers=SEC_UA)
    time.sleep(0.15)
    if facts:
        out["quarters"] = {
            "revenue": quarterly(facts, REV_TAGS, "USD"),
            "net_income": quarterly(facts, ["NetIncomeLoss"], "USD"),
            "eps_diluted": quarterly(facts, ["EarningsPerShareDiluted"], "USD/shares"),
        }
    subs = get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", headers=SEC_UA)
    time.sleep(0.15)
    if subs:
        r = subs.get("filings", {}).get("recent", {})
        fl = []
        for form, d, acc, doc in zip(r.get("form", []), r.get("filingDate", []),
                                     r.get("accessionNumber", []), r.get("primaryDocument", [])):
            if form in ("10-K", "10-Q", "8-K", "20-F", "6-K") and len(fl) < 8:
                url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{doc}"
                fl.append({"form": form, "date": d, "url": url})
        out["filings"] = fl
    return out


# ---------------- Finnhub ----------------
def fh(path: str, **params):
    if not FINNHUB:
        return None
    res = get(f"https://finnhub.io/api/v1/{path}", params={**params, "token": FINNHUB})
    time.sleep(1.1)  # free tier: 60 calls/min
    return res


# ---------------- FMP ----------------
def fmp(path: str, **params):
    if not FMP:
        return None
    return get(f"https://financialmodelingprep.com/stable/{path}", params={**params, "apikey": FMP})


def main() -> None:
    today = date.today()
    companies: dict[str, dict] = {k: {} for k in STOCKS}
    status = {"sec": 0, "finnhub": 0, "fmp": 0}

    ciks = sec_ciks()
    for k, t in STOCKS.items():
        cik = ciks.get(t)
        if cik:
            b = sec_block(cik)
            if b:
                companies[k].update(b)
                status["sec"] += 1

    calendar = []
    if FINNHUB:
        cal = fh("calendar/earnings", **{"from": (today - timedelta(days=7)).isoformat(),
                                         "to": (today + timedelta(days=75)).isoformat()}) or {}
        inv = {t: k for k, t in STOCKS.items()}
        for e in cal.get("earningsCalendar", []):
            k = inv.get(e.get("symbol"))
            if k:
                calendar.append({"key": k, "date": e.get("date"), "hour": e.get("hour"),
                                 "eps_est": e.get("epsEstimate"), "eps_act": e.get("epsActual"),
                                 "rev_est": e.get("revenueEstimate"), "rev_act": e.get("revenueActual")})
        for k, t in STOCKS.items():
            m = fh("stock/metric", symbol=t, metric="all") or {}
            mm = m.get("metric") or {}
            keep = ["peTTM", "psTTM", "pbAnnual", "marketCapitalization", "netProfitMarginTTM",
                    "grossMarginTTM", "operatingMarginTTM", "roeTTM", "revenueGrowthTTMYoy",
                    "epsGrowthTTMYoy", "totalDebt/totalEquityQuarterly", "dividendYieldIndicatedAnnual", "beta"]
            if mm:
                companies[k]["metrics"] = {x: mm.get(x) for x in keep if mm.get(x) is not None}
                status["finnhub"] += 1
            news = fh("company-news", symbol=t, **{"from": (today - timedelta(days=4)).isoformat(),
                                                    "to": today.isoformat()}) or []
            companies[k]["news"] = [{"t": n.get("headline", ""), "s": n.get("source", ""), "u": n.get("url", ""),
                                     "d": datetime.fromtimestamp(n.get("datetime", 0), timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
                                     "sum": (n.get("summary") or "")[:400]}
                                    for n in news[:8] if n.get("url", "").startswith("http")]
        calendar.sort(key=lambda x: x["date"] or "")

    if FMP:
        for k, t in STOCKS.items():
            if companies[k].get("quarters", {}).get("revenue"):
                continue  # SEC already covers it
            inc = fmp("income-statement", symbol=t, period="quarter", limit=8)
            if isinstance(inc, list) and inc:
                companies[k]["quarters"] = {
                    "revenue": [{"period": f"{x.get('fiscalYear')}{x.get('period')}", "end": x.get("date"), "val": x.get("revenue")} for x in reversed(inc)],
                    "net_income": [{"period": f"{x.get('fiscalYear')}{x.get('period')}", "end": x.get("date"), "val": x.get("netIncome")} for x in reversed(inc)],
                    "eps_diluted": [{"period": f"{x.get('fiscalYear')}{x.get('period')}", "end": x.get("date"), "val": x.get("epsDiluted")} for x in reversed(inc)],
                    "source": "FMP",
                }
                status["fmp"] += 1

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({"updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                               "status": status, "calendar": calendar, "companies": companies},
                              ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("done", status, "calendar", len(calendar))


if __name__ == "__main__":
    main()
