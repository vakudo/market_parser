from __future__ import annotations

import asyncio
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass

from market_parser.config import Settings
from market_parser.models import ProductPrice


class StoreAdapterError(RuntimeError):
    pass


class StoreBlockedError(StoreAdapterError):
    pass


class StoreRateLimitedError(StoreAdapterError):
    pass


@dataclass(frozen=True)
class StoreMetadata:
    slug: str
    name: str
    channel: str
    category_url: str


class BaseStoreAdapter(ABC):
    metadata: StoreMetadata
    requires_network: bool = True
    # False for stores that can only be collected manually (e.g. via the CDP
    # real-browser flow), so the daily automated run skips them.
    auto_runnable: bool = True
    # True for stores that need a scraping API key (the ServicePipe/Variti
    # stores). They join the daily run only when one is configured.
    requires_scrape_api: bool = False

    def __init__(self, settings: Settings):
        self.settings = settings

    @abstractmethod
    async def fetch_category(self, limit: int | None = None) -> list[ProductPrice]:
        raise NotImplementedError

    def effective_limit(self, limit: int | None) -> int | None:
        return limit

    def limit_reached(self, count: int, limit: int | None) -> bool:
        return limit is not None and count >= limit

    def remaining_limit(self, count: int, limit: int | None) -> int | None:
        if limit is None:
            return None
        return max(limit - count, 0)

    async def polite_page_delay(self) -> None:
        jitter = random.uniform(0, self.settings.page_delay_jitter_seconds)
        await asyncio.sleep(self.settings.page_delay_seconds + jitter)
