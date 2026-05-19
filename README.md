# Market Parser

Ежедневный сбор публично видимых цен на детское питание и выгрузка в SQLite, XLSX и Google Sheets.

## Что реализовано

- 5 магазинов первой версии: `ozon`, `wildberries`, `detmir`, `vprok`, `yandex_market`.
- SQLite как полный архив.
- Месячные XLSX в формате блоков по датам.
- Google Sheets-витрина последних 30 дней через service account.
- CLI для запуска на VPS и dry-run проверки.
- Systemd timer для ежедневного запуска в 09:00 по московскому времени.

## Быстрый старт

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
playwright install chromium
Copy-Item .env.example .env
market-parser init-db
market-parser run --stores wildberries,detmir --limit 20 --dry-run
```

Для полного запуска без ограничения:

```powershell
market-parser run
```

## Google Sheets

1. Создайте service account в Google Cloud.
2. Скачайте JSON-ключ на VPS.
3. Расшарьте Google Sheet на email service account.
4. Заполните в `.env`:

```env
GOOGLE_SHEET_ID=...
GOOGLE_SERVICE_ACCOUNT_FILE=/run/secrets/google-service-account.json
```

После обычного `market-parser run` таблица `Витрина_30д` будет перезаписана последними 30 днями.

## Формат выгрузки

Видимый лист содержит:

- `Сеть`
- `Категория`
- `Бренд`
- `Наименование товара`
- по каждой дате 3 колонки: регулярная цена, промо цена, цена по карте лояльности.

Если тип цены не найден, ячейка остаётся пустой.

## Команды

```bash
market-parser init-db
market-parser run --stores ozon,wildberries --limit 100
market-parser export-xlsx --month 2026-05
market-parser sync-google --days 30
market-parser stores
```

## Запуск на VPS

Вариант через Docker:

```bash
docker build -t market-parser .
docker run --rm --env-file .env -v "$PWD/data:/app/data" -v "$PWD/exports:/app/exports" market-parser run
```

Вариант через systemd: скопируйте файлы из `deploy/`, замените пути в `market-parser.service`, затем:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now market-parser.timer
```

## Ограничения первой версии

- Не используются личные аккаунты магазинов.
- Капчи и антибот-страницы не обходятся; ошибка фиксируется в `runs`, остальные сети продолжают сбор.
- Для сетей с адресной ценой применяется публичная выдача для Москвы/центрального региона, насколько сайт отдаёт её без авторизации.
