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
SEC_UA = {"User-Agent": "Aldiyar Berik Aldos24@users.noreply.github.com", "Accept-Encoding": "gzip, deflate"}
# fallback CIKs; each is verified against the tickers listed in SEC submissions
CIK_FALLBACK = {"NVDA": 1045810, "AVGO": 1730168, "AMD": 2488, "TSM": 1046179, "ASML": 937966, "MU": 723125,
    "MSFT": 789019, "GOOGL": 1652044, "AMZN": 1018724, "META": 1326801, "ORCL": 1341439, "AAPL": 320193,
    "ANET": 1596532, "ETN": 1551182, "VRT": 1674101, "CEG": 1868275, "NEE": 753308, "PLTR": 1321655,
    "FRHC": 924805, "MLI": 89439, "LII": 1069202, "WTS": 795403}

# terminal key -> exchange ticker (stocks only)
STOCKS = {
    "NVDA": "NVDA", "AVGO": "AVGO", "AMD": "AMD", "TSM": "TSM", "ASML": "ASML", "MU": "MU",
    "MSFT": "MSFT", "GOOGL": "GOOGL", "AMZN": "AMZN", "META": "META", "ORCL": "ORCL", "AAPL": "AAPL",
    "ANET": "ANET", "ETN": "ETN", "VRT": "VRT", "CEG": "CEG", "NEE": "NEE", "CRWV": "CRWV",
    "PLTR": "PLTR", "KSPI": "KSPI", "FRHC": "FRHC", "MLI": "MLI", "LII": "LII", "WTS": "WTS",
}
# words that must appear in a headline/summary for company news to count
NAME_HINTS = {"NVDA": ["nvidia"], "AVGO": ["broadcom"], "AMD": ["amd", "advanced micro"], "TSM": ["tsmc", "taiwan semi"],
    "ASML": ["asml"], "MU": ["micron"], "MSFT": ["microsoft"], "GOOGL": ["google", "alphabet"], "AMZN": ["amazon", "aws"],
    "META": ["meta", "facebook", "instagram"], "ORCL": ["oracle"], "AAPL": ["apple", "iphone"], "ANET": ["arista"],
    "ETN": ["eaton"], "VRT": ["vertiv"], "CEG": ["constellation"], "NEE": ["nextera", "fpl"], "CRWV": ["coreweave"],
    "PLTR": ["palantir"], "KSPI": ["kaspi"], "FRHC": ["freedom"], "MLI": ["mueller"], "LII": ["lennox"], "WTS": ["watts"]}
REV_CONCEPTS = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax"]
REV_TAGS = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"]


def get(url: str, tries: int = 3, **kw):
    for i in range(tries):
        try:
            r = requests.get(url, timeout=30, **kw)
            if r.status_code == 429:
                time.sleep(5 * (i + 1))
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            if i == tries - 1:
                print(f"  {url.split('?')[0][-70:]}: {e}")
            time.sleep(1.5)
    return None


# ---------------- SEC EDGAR ----------------
def sec_ciks() -> dict[str, int]:
    j = get("https://www.sec.gov/files/company_tickers.json", tries=1, headers=SEC_UA) or {}
    m = {v["ticker"].upper(): int(v["cik_str"]) for v in j.values()}
    print("  SEC ticker map:", len(m))
    return {**CIK_FALLBACK, **m}


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
        out["sec_tickers"] = subs.get("tickers", [])
        out["name"] = subs.get("name")
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
    # SEC blocks many cloud IPs (incl. GitHub runners): probe once, skip if refused
    probe = get(f"https://data.sec.gov/submissions/CIK{ciks['MLI']:010d}.json", tries=1, headers=SEC_UA)
    if not probe:
        print("  SEC unreachable from this runner, using FMP/Finnhub only")
    for k, t in (STOCKS.items() if probe else []):
        cik = ciks.get(t)
        if cik:
            b = sec_block(cik)
            if b.get("sec_tickers") is not None and t not in b["sec_tickers"]:
                print(f"  {k}: CIK {cik} is {b.get('name')}, skipped")
                continue
            b.pop("sec_tickers", None)
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
            hints = NAME_HINTS.get(k, [k.lower()]) + [t.lower()]
            news = [n for n in news if any(h in ((n.get("headline") or "")).lower() for h in hints)]
            companies[k]["news"] = [{"t": n.get("headline", ""), "s": n.get("source", ""), "u": n.get("url", ""),
                                     "d": datetime.fromtimestamp(n.get("datetime", 0), timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
                                     "sum": (n.get("summary") or "")[:400]}
                                    for n in news[:8] if n.get("url", "").startswith("http")]
            # EPS history: actual vs consensus (last 4 quarters)
            er = fh("stock/earnings", symbol=t) or []
            if isinstance(er, list) and er:
                companies[k]["eps_hist"] = [{"period": e.get("period"), "q": e.get("quarter"), "y": e.get("year"),
                                             "act": e.get("actual"), "est": e.get("estimate"),
                                             "surp": e.get("surprisePercent")} for e in er[:4]]
            # quarterly financials as reported to the SEC (via Finnhub)
            fr = fh("stock/financials-reported", symbol=t, freq="quarterly") or {}
            rows = []
            for rep in (fr.get("data") or [])[:9]:
                ic = {(x.get("concept") or "").split("_")[-1]: x.get("value") for x in (rep.get("report") or {}).get("ic") or []}
                rev = next((ic[c] for c in REV_CONCEPTS if ic.get(c) is not None), None)
                rows.append({"y": rep.get("year"), "q": rep.get("quarter"), "start": (rep.get("startDate") or "")[:10],
                             "period": f"{rep.get('year')}Q{rep.get('quarter')}", "end": (rep.get("endDate") or "")[:10],
                             "form": rep.get("form"), "filed": (rep.get("filedDate") or "")[:10],
                             "rev": rev, "ni": ic.get("NetIncomeLoss") or ic.get("ProfitLoss"),
                             "eps": ic.get("EarningsPerShareDiluted")})
            rows = list(reversed(rows))
            # 10-Q income statements are often year-to-date: subtract the previous quarter of the same fiscal year
            for i in range(len(rows) - 1, 0, -1):
                a, b = rows[i], rows[i - 1]
                ytd = a["start"] and a["start"] == b["start"]
                if ytd and a["y"] == b["y"] and a["q"] == (b["q"] or 0) + 1:
                    for f in ("rev", "ni"):
                        if a[f] is not None and b[f] is not None:
                            a[f] = a[f] - b[f]
                    a["eps"] = None  # per-share YTD figures can't be split reliably
                    a["split"] = True

            def is_quarter(r):
                try:
                    days = (date.fromisoformat(r["end"]) - date.fromisoformat(r["start"])).days
                except ValueError:
                    return True
                return days < 100 or r.get("split")
            rows = [r for r in rows if is_quarter(r)]
            fin = [{x: r[x] for x in ("period", "end", "form", "filed", "rev", "ni", "eps")} for r in rows]
            if fin:
                companies[k]["fin"] = fin[-8:]
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
