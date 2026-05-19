FROM mcr.microsoft.com/playwright/python:v1.56.0-noble

WORKDIR /app

COPY pyproject.toml README.md ./
COPY market_parser ./market_parser

RUN pip install --no-cache-dir .

ENV MARKET_PARSER_DB_PATH=/app/data/market_parser.sqlite \
    MARKET_PARSER_EXPORT_DIR=/app/exports \
    MARKET_PARSER_TIMEZONE=Europe/Moscow

VOLUME ["/app/data", "/app/exports"]

ENTRYPOINT ["market-parser"]
CMD ["run"]
