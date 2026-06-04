from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from .models import ProductPrice
from .normalize import product_key

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    status TEXT NOT NULL,
    stores_json TEXT NOT NULL,
    total_products INTEGER NOT NULL DEFAULT 0,
    errors_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_key TEXT NOT NULL UNIQUE,
    store_slug TEXT NOT NULL,
    store_name TEXT NOT NULL,
    store_product_id TEXT NOT NULL,
    category TEXT NOT NULL,
    brand TEXT NOT NULL,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_products_store ON products(store_slug);

CREATE TABLE IF NOT EXISTS price_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    run_id INTEGER REFERENCES runs(id) ON DELETE SET NULL,
    observed_date TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    regular_price_kopecks INTEGER,
    promo_price_kopecks INTEGER,
    loyalty_price_kopecks INTEGER,
    rating REAL,
    currency TEXT NOT NULL DEFAULT 'RUB',
    availability TEXT NOT NULL,
    status TEXT NOT NULL,
    raw_error TEXT,
    UNIQUE(product_id, observed_date)
);

CREATE INDEX IF NOT EXISTS idx_observations_date ON price_observations(observed_date);
CREATE INDEX IF NOT EXISTS idx_observations_product_date
    ON price_observations(product_id, observed_date);
"""


class Storage:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)

    @contextmanager
    def connect(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            self._migrate(conn)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(price_observations)").fetchall()
        }
        if "rating" not in columns:
            conn.execute("ALTER TABLE price_observations ADD COLUMN rating REAL")

    def start_run(self, store_slugs: list[str]) -> int:
        self.init_db()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO runs(started_at, status, stores_json)
                VALUES (?, ?, ?)
                """,
                (
                    datetime.now(tz=UTC).isoformat(),
                    "running",
                    json.dumps(store_slugs, ensure_ascii=False),
                ),
            )
            return int(cursor.lastrowid)

    def finish_run(
        self,
        run_id: int,
        *,
        status: str,
        total_products: int,
        errors: list[dict[str, Any]],
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE runs
                SET ended_at = ?, status = ?, total_products = ?, errors_json = ?
                WHERE id = ?
                """,
                (
                    datetime.now(tz=UTC).isoformat(),
                    status,
                    total_products,
                    json.dumps(errors, ensure_ascii=False),
                    run_id,
                ),
            )

    def save_prices(
        self,
        products: Iterable[ProductPrice],
        run_id: int | None = None,
        *,
        replace_store_day: bool = False,
    ) -> int:
        self.init_db()
        products = list(products)
        saved = 0
        with self.connect() as conn:
            if replace_store_day:
                self._delete_store_day_observations(conn, products)
            for product in products:
                product_db_id = self._upsert_product(conn, product)
                self._upsert_observation(conn, product_db_id, product, run_id)
                saved += 1
        return saved

    def _delete_store_day_observations(
        self,
        conn: sqlite3.Connection,
        products: list[ProductPrice],
    ) -> None:
        store_dates = {
            (product.store_slug, product.observed_date.isoformat()) for product in products
        }
        for store_slug, observed_date in store_dates:
            conn.execute(
                """
                DELETE FROM price_observations
                WHERE observed_date = ?
                  AND product_id IN (
                    SELECT id FROM products WHERE store_slug = ?
                  )
                """,
                (observed_date, store_slug),
            )

    def _upsert_product(self, conn: sqlite3.Connection, product: ProductPrice) -> int:
        now = product.collected_at.isoformat()
        key = product_key(
            product.store_slug,
            product.product_id,
            product.product_url,
            product.product_name,
        )
        conn.execute(
            """
            INSERT INTO products(
                product_key, store_slug, store_name, store_product_id, category,
                brand, name, url, first_seen_at, last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(product_key) DO UPDATE SET
                store_name = excluded.store_name,
                store_product_id = excluded.store_product_id,
                category = excluded.category,
                brand = excluded.brand,
                name = excluded.name,
                url = excluded.url,
                last_seen_at = excluded.last_seen_at
            """,
            (
                key,
                product.store_slug,
                product.store_name,
                product.product_id,
                product.category,
                product.brand,
                product.product_name,
                product.product_url,
                now,
                now,
            ),
        )
        row = conn.execute("SELECT id FROM products WHERE product_key = ?", (key,)).fetchone()
        if row is None:
            raise RuntimeError(f"Failed to upsert product {key}")
        return int(row["id"])

    def _upsert_observation(
        self,
        conn: sqlite3.Connection,
        product_db_id: int,
        product: ProductPrice,
        run_id: int | None,
    ) -> None:
        observed_date = product.observed_date.isoformat()
        raw_error = None
        if product.status != "ok":
            raw_error = (
                json.dumps(product.raw, ensure_ascii=False) if product.raw else product.status
            )
        conn.execute(
            """
            INSERT INTO price_observations(
                product_id, run_id, observed_date, collected_at,
                regular_price_kopecks, promo_price_kopecks, loyalty_price_kopecks,
                rating, currency, availability, status, raw_error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(product_id, observed_date) DO UPDATE SET
                run_id = excluded.run_id,
                collected_at = excluded.collected_at,
                regular_price_kopecks = excluded.regular_price_kopecks,
                promo_price_kopecks = excluded.promo_price_kopecks,
                loyalty_price_kopecks = excluded.loyalty_price_kopecks,
                rating = excluded.rating,
                currency = excluded.currency,
                availability = excluded.availability,
                status = excluded.status,
                raw_error = excluded.raw_error
            """,
            (
                product_db_id,
                run_id,
                observed_date,
                product.collected_at.isoformat(),
                product.regular_price_kopecks,
                product.promo_price_kopecks,
                product.loyalty_price_kopecks,
                product.rating,
                product.currency,
                product.availability,
                product.status,
                raw_error,
            ),
        )

    def fetch_observed_dates(self, start_date: date, end_date: date) -> list[date]:
        self.init_db()
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT observed_date
                FROM price_observations
                WHERE observed_date BETWEEN ? AND ?
                ORDER BY observed_date
                """,
                (start_date.isoformat(), end_date.isoformat()),
            ).fetchall()
        return [date.fromisoformat(row["observed_date"]) for row in rows]

    def fetch_price_rows(self, start_date: date, end_date: date) -> list[dict[str, Any]]:
        self.init_db()
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    p.store_slug, p.store_name, p.category, p.brand, p.name, p.url,
                    o.observed_date, o.regular_price_kopecks, o.promo_price_kopecks,
                    o.loyalty_price_kopecks, o.rating, o.currency, o.availability, o.status
                FROM products p
                JOIN price_observations o ON o.product_id = p.id
                WHERE o.observed_date BETWEEN ? AND ?
                ORDER BY p.store_name, p.category, p.brand, p.name, o.observed_date
                """,
                (start_date.isoformat(), end_date.isoformat()),
            ).fetchall()
        return [dict(row) for row in rows]

    def fetch_latest_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        self.init_db()
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, started_at, ended_at, status, stores_json, total_products, errors_json
                FROM runs
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]


def group_observations(
    rows: list[dict[str, Any]],
) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    observations: dict[tuple[str, str, str, str], dict[str, dict[str, Any]]] = defaultdict(dict)

    for row in rows:
        key = (row["store_name"], row["category"], row["brand"], row["name"])
        grouped.setdefault(
            key,
            {
                "store_name": row["store_name"],
                "category": row["category"],
                "brand": row["brand"],
                "name": row["name"],
                "url": row["url"],
                "observations": observations[key],
            },
        )
        observations[key][row["observed_date"]] = row
    return grouped
