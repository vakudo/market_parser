# Версия образа должна совпадать с пином playwright в pyproject.toml (<1.50,
# иначе драйвер несовместим с camoufox 0.4.x и браузеры падают на старте).
FROM mcr.microsoft.com/playwright/python:v1.48.0-noble

WORKDIR /app

COPY pyproject.toml README.md ./
COPY market_parser ./market_parser

RUN pip install --no-cache-dir .

# Антибот-браузер Camoufox (для ozon, wildberries, lenta и др.) — качаем на этапе сборки.
RUN python -m camoufox fetch

# Настоящий Chrome + Xvfb для Variti-магазинов (samokat/perekrestok/onlinetrade):
# patchright водит системный Chrome headful под виртуальным дисплеем.
RUN apt-get update \
    && apt-get install -y --no-install-recommends wget gnupg xvfb \
    && wget -qO- https://dl.google.com/linux/linux_signing_key.pub \
       | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" \
       > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# Часовой пояс контейнера = московский: согласован с IP резидентного прокси,
# чтобы Variti не видел рассинхрон таймзоны браузера и адреса.
ENV TZ=Europe/Moscow

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
