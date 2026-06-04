# Market Parser

Ежедневный сбор публичных цен на **детское питание** из российских магазинов и маркетплейсов с выгрузкой в SQLite, XLSX и Google Sheets.

## Что собирается

**19 магазинов.** 16 из них собираются автоматически, 3 — только вручную (см. ниже).

- **Авто (16):** `wildberries`, `ozon`, `detmir`, `metro`, `auchan`, `magnit`, `dixy`,
  `pyaterochka`, `lenta`, `yandex_market`, `chizhik`, `vkusvill`, `yandex_lavka`,
  `vprok`, `krasnoe_beloe`, `komus`.
- **Только вручную (3, через реальный браузер):** `samokat`, `perekrestok`, `onlinetrade`
  — у них антибот **Variti**, который не пропускает автоматизированный браузер. Собираются
  скриптами `run_logs/cdp_*.py` через CDP-подключение к твоему Chrome (см. «Антибот-магазины»).

По каждому товару: сеть, бренд, наименование, **ссылка**, **рейтинг** (где есть), и цены
(регулярная / со скидкой / по карте лояльности) по каждой дате.

## Установка

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m camoufox fetch        # антибот-браузер (для ozon, wildberries, lenta и др.)
playwright install chromium     # для части магазинов и CDP
Copy-Item .env.example .env
python -m market_parser.cli init-db
```

## Настройка (.env)

```env
# Google Sheets (необязательно, но нужно для авто-выгрузки в таблицу)
GOOGLE_SHEET_ID=<id из ссылки на таблицу>
GOOGLE_SHEET_NAME=Витрина_30д
GOOGLE_SERVICE_ACCOUNT_FILE=google-sa.json

# Прокси для браузера (нужно на VM/датацентре — см. «Важно про IP»)
# MARKET_PARSER_BROWSER_PROXY_SERVER=http://host:port
# MARKET_PARSER_BROWSER_PROXY_USERNAME=...
# MARKET_PARSER_BROWSER_PROXY_PASSWORD=...
```

**Google Sheets:** создай service account в Google Cloud, включи Google Sheets API, скачай
JSON-ключ в корень проекта как `google-sa.json`, и поделись таблицей с email сервисного
аккаунта (поле `client_email` в JSON) с правами «Редактор».

## Использование

```bash
python -m market_parser.cli run --auto            # дневной прогон: 16 авто-магазинов + XLSX + Google
python -m market_parser.cli run                    # все магазины (3 CDP-магазина отвалятся)
python -m market_parser.cli run --stores ozon,wildberries --limit 50
python -m market_parser.cli run --auto --dry-run   # без записи в БД/XLSX/Google
python -m market_parser.cli export-xlsx --month 2026-06
python -m market_parser.cli sync-google --days 30
python -m market_parser.cli stores                 # список магазинов
```

`run --auto` — это и есть то, что запускается по расписанию: собирает, пишет
`exports/<YYYY-MM>.xlsx` и синхронизирует Google Sheets за последние 30 дней.

## Формат выгрузки

Один лист «Выгрузка»:

| Сеть | Бренд | Наименование товара | Ссылка | Рейтинг | дата → 3 колонки цен… |
|------|-------|---------------------|--------|---------|------------------------|

- «Ссылка» — кликабельная ссылка на товар.
- По каждой дате три колонки: регулярная цена, цена со скидкой, цена по карте лояльности
  (пустые, если магазин такой тип не отдаёт).
- Полный архив — в SQLite (`data/market_parser.sqlite`).

## Автоматизация: обновление каждый день в 09:00

### Windows (Task Scheduler)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_schedule.ps1
```

Создаёт задачу `MarketParserDaily`, которая каждый день в 09:00 запускает
`scripts\daily_update.ps1` (логи — в `logs\`). Запустить вручную для проверки:
`Start-ScheduledTask -TaskName MarketParserDaily`. Удалить:
`Unregister-ScheduledTask -TaskName MarketParserDaily -Confirm:$false`.

### Linux / VM (cron)

```bash
chmod +x scripts/daily_update.sh
crontab -e
# добавить строку (09:00 каждый день):
0 9 * * * /полный/путь/market_parser/scripts/daily_update.sh
```

> Если нужно по московскому времени на UTC-сервере — поставь `0 6 * * *` или задай `TZ=Europe/Moscow` в crontab.

## ⚠️ Важно про IP (иначе антибот-магазины не соберутся)

Магазины с антиботом (Wildberries, Ozon, Детский мир, Магнит, Чижик, Пятёрочка, Лента и др.)
пускают только **российские «домашние» (резидентные) IP**. С обычного **датацентрового IP
(облачная VM, VPN)** их антибот блокирует, и магазин вернёт 0 товаров.

Варианты для VM/сервера:

1. **Резидентный российский прокси** — прописать `MARKET_PARSER_BROWSER_PROXY_SERVER` в `.env`.
2. **Запуск на домашней машине** с российским провайдером (тогда прокси не нужен).
3. Если на машине включён VPN ради других задач — настрой **split tunneling**, чтобы
   `python.exe` и браузер Camoufox шли мимо VPN (домашним IP).

Google Sheets API при этом должен оставаться доступен (через VPN/обычный канал).

## Антибот-магазины (Samokat / Perekrestok / Onlinetrade)

У них защита Variti, которая ловит автоматизацию даже в Camoufox. Собираются **вручную**
через твой настоящий Chrome:

1. Полностью закрой Chrome, затем запусти отладочный экземпляр:
   ```powershell
   & "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="$env:TEMP\chrome-debug"
   ```
2. Открой нужный магазин, при капче — реши её руками, дождись товаров.
3. Запусти сборщик: `python run_logs/cdp_collect.py` (Самокат),
   `run_logs/cdp_pk_collect.py` (Перекрёсток), `run_logs/cdp_ot_collect.py` (Онлайнтрейд).

Эти данные дописываются в ту же БД/таблицу. В дневной авто-прогон они не входят.



## Тесты

```bash
python -m pytest -q
```
