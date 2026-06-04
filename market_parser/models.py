from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(frozen=True)
class ProductPrice:
    store_slug: str
    store_name: str
    category: str
    brand: str
    product_name: str
    product_url: str
    product_id: str
    regular_price_kopecks: int | None
    promo_price_kopecks: int | None = None
    loyalty_price_kopecks: int | None = None
    rating: float | None = None
    currency: str = "RUB"
    availability: str = "unknown"
    status: str = "ok"
    collected_at: datetime = field(default_factory=utc_now)
    raw: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)

    @property
    def observed_date(self) -> date:
        return self.collected_at.date()


@dataclass(frozen=True)
class StoreRunResult:
    store_slug: str
    store_name: str
    count: int
    status: str
    error: str | None = None
    duration_seconds: float | None = None


@dataclass(frozen=True)
class PriceMatrix:
    dates: list[date]
    rows: list[list[Any]]
