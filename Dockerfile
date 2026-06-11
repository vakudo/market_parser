FROM mcr.microsoft.com/playwright/python:v1.56.0-noble

WORKDIR /app

COPY pyproject.toml README.md ./
COPY market_parser ./market_parser

RUN pip install --no-cache-dir .

# Антибот-браузер Camoufox (для ozon, wildberries, lenta и др.) — качаем на этапе сборки.
RUN python -m camoufox fetch

# PYTHONUNBUFFERED — чтобы прогресс прогона появлялся в логах Railway сразу, а не в конце.
ENV PYTHONUNBUFFERED=1 \
    MARKET_PARSER_DB_PATH=/app/data/market_parser.sqlite \
    MARKET_PARSER_EXPORT_DIR=/app/exports \
    MARKET_PARSER_TIMEZONE=Europe/Moscow

# Том для /app/data (история цен) подключается снаружи: Railway Volume или `docker -v`.
# Инструкцию VOLUME не используем — билдер Railway её не поддерживает.

ENTRYPOINT ["market-parser"]
# Дневной cron-прогон: собрать 16 авто-магазинов, выгрузить XLSX/Google и
# отправить сводку + файл в Telegram. Процесс завершается — как и нужно cron-сервису Railway.
CMD ["run", "--auto", "--telegram"]
