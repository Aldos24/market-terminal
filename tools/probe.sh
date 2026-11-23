#!/usr/bin/env bash
# Probe candidate data sources; prints HTTP status, content type and the first bytes.
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
probe() { echo "=== $1"; curl -sL -m 40 -A "$UA" -o /tmp/p.out -w "HTTP %{http_code} | %{content_type} | %{size_download} bytes\n" "$1"; head -c "${2:-700}" /tmp/p.out | tr -d '\r'; echo; }
D=$(date -u -d "-3 days" +%d.%m.%Y)
probe "https://nationalbank.kz/rss/get_rates.cfm?fdate=$D" 900
probe "https://nationalbank.kz/rss/rates_all.xml" 400
probe "https://nationalbank.kz/ru/news/bazovaya-stavka" 300
probe "https://nationalbank.kz/ru/page/bazovaya-stavka" 300
probe "https://kase.kz/ru/money_market/repo_indicators/tonia/" 300
probe "https://kase.kz/ru/indexes/KASE/" 300
probe "https://kase.kz/api/v1/indexes/KASE" 300
probe "https://api.statistiken.bundesbank.de/rest/data/BBSIS/D.I.ZST.ZI.EUR.S1311.B.A604.R02XX.R.A.A._Z._Z.A?format=csv&lastNObservations=5" 900
probe "https://data-api.ecb.europa.eu/service/data/YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_2Y?format=csvdata&lastNObservations=3" 600
