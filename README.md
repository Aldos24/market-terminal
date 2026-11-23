# market-terminal

Ценовой конвейер для личного рыночного терминала.

- `assets.csv` — список активов (57): ключ, тикер Yahoo, серия FRED, группа, описание.
- `update_prices.py` — качает ~13 месяцев дневных закрытий (yfinance, FRED для доходностей UST) и пишет `data/`.
- `.github/workflows/update-prices.yml` — запуск каждый будний день в 23:30 UTC (04:30 Алматы) + ручной запуск.
- `data/prices.json` — данные для терминала; `data/prices.csv` — та же таблица для pandas/Excel; `data/status.json` — что обновилось и что нет.

Каждый запуск перекачивает всё окно целиком, поэтому пропущенный или упавший день чинится следующим запуском.

Добавить актив: строка в `assets.csv` → Actions → update-prices → Run workflow.
