"""Headline feed per asset for the market terminal.

For every asset in assets.csv collects recent headlines from RSS:
  - Yahoo Finance headline feed for listed stocks/ETFs (by ticker);
  - Google News search (query per asset; Russian-language queries for Kazakhstan).
Writes data/news.json: {"updated", "items": {KEY: [{"t","s","u","d"}]}}
(title, source, url, ISO datetime). Keeps 14 days, max 15 headlines per asset.
Headlines only - no article text is stored.
"""
from __future__ import annotations

import csv
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote_plus

import requests

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "news.json"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/124 Safari/537.36"}
DAYS, PER_ASSET = 14, 15

# Google News queries: (language, query). Assets not listed use their name.
QUERIES: dict[str, tuple[str, str]] = {
    "US2Y": ("en", "2-year Treasury yield"),
    "US10Y": ("en", "10-year Treasury yield"),
    "BUND2Y": ("en", "German bund yields"),
    "DXY": ("en", "dollar index DXY"),
    "EURUSD": ("en", "euro dollar EUR/USD"),
    "USDJPY": ("en", "yen dollar USD/JPY"),
    "USDKZT": ("ru", "курс тенге"),
    "USDRUB": ("ru", "курс рубля доллар"),
    "USDCNH": ("en", "yuan dollar"),
    "TONIA": ("ru", "денежный рынок Казахстан TONIA"),
    "NBKRATE": ("ru", "Нацбанк базовая ставка"),
    "BRENT": ("en", "Brent crude oil price"),
    "GOLD": ("en", "gold price"),
    "SPX": ("en", "S&P 500 stocks"),
    "VIX": ("en", "VIX volatility"),
    "BTC": ("en", "bitcoin price"),
    "SOX": ("en", "semiconductor stocks"),
    "COPPER": ("en", "copper price"),
    "RSP": ("en", "equal-weight S&P 500 market breadth"),
    "IWM": ("en", "Russell 2000 small caps"),
    "HYG": ("en", "high-yield bonds credit spreads"),
    "TLT": ("en", "long-term Treasuries bond market"),
    "XLF": ("en", "bank stocks financials"),
    "XLE": ("en", "energy stocks oil companies"),
    "STOXX50": ("en", "European stocks Euro Stoxx"),
    "N225": ("en", "Nikkei Japan stocks"),
    "EEM": ("en", "emerging markets stocks"),
    "HSI": ("en", "Hang Seng Hong Kong stocks"),
    "KASE": ("ru", "индекс KASE"),
    "KSPI": ("en", "Kaspi.kz"),
    "HSBK": ("en", "Halyk Bank"),
    "KAP": ("en", "Kazatomprom"),
    "FRHC": ("en", "Freedom Holding FRHC"),
    "MLI": ("en", "Mueller Industries"),
    "ITB": ("en", "homebuilder stocks"),
    "LII": ("en", "Lennox International"),
    "WTS": ("en", "Watts Water Technologies"),
    "IJJ": ("en", "mid-cap value stocks"),
}


def is_listed(yahoo: str) -> bool:
    t = yahoo.split("|")[0]
    return bool(t) and not any(c in t for c in "^=") and not t.endswith("-USD")


def parse_rss(xml: bytes) -> list[dict]:
    items = []
    root = ET.fromstring(xml)
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        src_el = it.find("source")
        src = (src_el.text or "").strip() if src_el is not None else ""
        if not src and " - " in title:
            title, src = title.rsplit(" - ", 1)
        try:
            d = parsedate_to_datetime(it.findtext("pubDate") or "").astimezone(timezone.utc)
        except Exception:  # noqa: BLE001
            continue
        if title and link.startswith("http"):
            items.append({"t": title, "s": src or "Yahoo Finance", "u": link, "d": d})
    return items


def fetch(url: str) -> list[dict]:
    try:
        r = requests.get(url, timeout=20, headers=UA)
        r.raise_for_status()
        return parse_rss(r.content)
    except Exception as e:  # noqa: BLE001
        print(f"  {url[:90]}: {e}")
        return []


def main() -> None:
    assets = list(csv.DictReader(open(ROOT / "assets.csv", encoding="utf-8")))
    cutoff = datetime.now(timezone.utc) - timedelta(days=DAYS)
    out: dict[str, list[dict]] = {}
    for a in assets:
        k = a["key"]
        got: list[dict] = []
        if is_listed(a["yahoo"]):
            t = a["yahoo"].split("|")[0]
            got += fetch(f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={quote_plus(t)}&region=US&lang=en-US")
        lang, q = QUERIES.get(k, ("en", a["name"]))
        if lang == "ru":
            url = f"https://news.google.com/rss/search?q={quote_plus(q + ' when:7d')}&hl=ru&gl=KZ&ceid=KZ:ru"
        else:
            url = f"https://news.google.com/rss/search?q={quote_plus(q + ' when:7d')}&hl=en-US&gl=US&ceid=US:en"
        got += fetch(url)
        seen, clean = set(), []
        for it in sorted(got, key=lambda x: x["d"], reverse=True):
            key = re.sub(r"\W+", "", it["t"].lower())[:80]
            if it["d"] < cutoff or key in seen:
                continue
            seen.add(key)
            clean.append({**it, "d": it["d"].strftime("%Y-%m-%dT%H:%MZ")})
            if len(clean) >= PER_ASSET:
                break
        out[k] = clean
        print(f"{k}: {len(clean)}")
        time.sleep(0.4)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({"updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                               "items": out}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("total", sum(len(v) for v in out.values()))


if __name__ == "__main__":
    main()
