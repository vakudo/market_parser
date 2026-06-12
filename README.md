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
  Пробовали обойти через patchright (stealth-Chrome) на сервере — Variti всё равно блокирует.

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
python -m market_parser.cli run --auto            # дневной прогон: все 19 магазинов + XLSX + Google
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

## Telegram-бот: файл каждое утро

После дневного прогона бот присылает в чат сводку (какие магазины собрались, сколько
товаров) и сам XLSX-файл за текущий месяц.

### Настройка бота

1. Создай бота через [@BotFather](https://t.me/BotFather) (`/newbot`) — получишь токен.
2. Пропиши в `.env`: `TELEGRAM_BOT_TOKEN=<токен>`.
3. **Обязательно** напиши своему боту любое сообщение (например `/start`) — бот не может
   писать первым. Затем узнай свой chat id любым из способов:
   ```bash
   python -m market_parser.cli telegram-chat-id
   ```
   или открой в браузере `https://api.telegram.org/bot<токен>/getUpdates` (слово `bot` и
   токен — слитно) и найди в ответе `"chat":{"id":...}`. Апдейты хранятся 24 часа: если
   ответ пустой — напиши боту ещё раз и обнови страницу.
4. Пропиши `TELEGRAM_CHAT_ID=<id>` в `.env`. Проверка: `python -m market_parser.cli send-telegram`
   — в чат придёт XLSX за текущий месяц.

### Команды

```bash
python -m market_parser.cli run --auto --telegram   # дневной прогон + отправка в TG
python -m market_parser.cli send-telegram           # отправить XLSX текущего месяца без сбора
python -m market_parser.cli send-telegram --month 2026-05 --message "Архив за май"
```

> Если локально команды падают с `ConnectError [WinError 10054]` — провайдер режет
> api.telegram.org по TLS-отпечатку Python (PowerShell/браузер при этом работают).
> На Railway это не воспроизводится. Локальный обход — запустить VPN-клиент с локальным
> прокси и задать `HTTPS_PROXY=http://127.0.0.1:<port>` перед командой, либо слать через
> `curl.exe -F document=@exports/<месяц>.xlsx ...sendDocument`.

## Деплой на Railway (авто-отправка каждое утро)

Репозиторий уже содержит всё нужное: `Dockerfile` (команда по умолчанию —
`run --auto --telegram`) и `railway.json` с cron-расписанием `0 6 * * *`
(06:00 UTC = 09:00 МСК). Railway запускает контейнер по расписанию, тот собирает цены,
шлёт файл в Telegram и завершается.

1. Запушь репозиторий на GitHub.
2. На [railway.app](https://railway.app): **New Project → Deploy from GitHub repo**.
3. В сервисе добавь **Volume** (правый клик по сервису → Attach Volume) с mount path
   `/app/data` — там живёт SQLite с историей цен (без него каждый прогон будет «с нуля»,
   и в файле будет только одна дата). В сам `Dockerfile` инструкцию `VOLUME` добавлять
   нельзя — билдер Railway её не поддерживает и роняет сборку.
4. В **Variables** задай:
   - `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`;
   - `MARKET_PARSER_BROWSER_PROXY_SERVER` (+ `..._USERNAME` / `..._PASSWORD`) —
     **резидентный российский прокси, без него почти все магазины вернут 0** (см. раздел про IP ниже);
   - для Google Sheets (опционально): `GOOGLE_SHEET_ID` и `GOOGLE_SERVICE_ACCOUNT_JSON`
     (вставь содержимое `google-sa.json` одной строкой — файла на Railway нет);
   - `MARKET_PARSER_STORE_CONCURRENCY=2` — рекомендуется: меньше одновременных
     браузеров, меньше пиковая память (при OOM-остановках контейнера поставь `1`).
5. Проверь, что в **Settings → Cron Schedule** подтянулось `0 6 * * *` из `railway.json`
   (расписание указывается в **UTC**). Для разовой проверки нажми **Redeploy** —
   для cron-сервиса это разовый запуск прогона; в Telegram должна прийти сводка и файл
   (полный прогон занимает десятки минут).

> Контейнер должен завершаться после прогона — это штатное поведение cron-сервиса Railway,
> «Restart Policy: Never» уже задан в `railway.json`.

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

У них защита Variti, которая ловит автоматизацию даже в Camoufox. Теперь они входят в
авто-прогон: `market_parser/stores/stealth.py` водит **настоящий Chrome** через patchright
(без следов автоматизации), профиль хранится в `data/browser_states/stealth_<магазин>` —
куки Variti накапливаются и переживают перезапуски. Нужны: установленный Google Chrome
(локально уже есть; в Docker ставится при сборке) и российский резидентный IP.

Если Variti всё-таки упёрся в капчу — есть запасной ручной путь через CDP:

1. Полностью закрой Chrome, затем запусти отладочный экземпляр:
   ```powershell
   & "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="$env:TEMP\chrome-debug"
   ```
2. Открой нужный магазин, при капче — реши её руками, дождись товаров.
3. Запусти сборщик: `python run_logs/cdp_collect.py` (Самокат),
   `run_logs/cdp_pk_collect.py` (Перекрёсток), `run_logs/cdp_ot_collect.py` (Онлайнтрейд).

Эти данные дописываются в ту же БД/таблицу.



## Тесты

```bash
python -m pytest -q
```
