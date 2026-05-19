from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from market_parser.config import Settings
from market_parser.models import ProductPrice


class StoreAdapterError(RuntimeError):
    pass


class StoreBlockedError(StoreAdapterError):
    pass


@dataclass(frozen=True)
class StoreMetadata:
    slug: str
    name: str
    channel: str
    category_url: str


class BaseStoreAdapter(ABC):
    metadata: StoreMetadata

    def __init__(self, settings: Settings):
        self.settings = settings

    @abstractmethod
    async def fetch_category(self, limit: int | None = None) -> list[ProductPrice]:
        raise NotImplementedError

    def effective_limit(self, limit: int | None) -> int:
        return limit or self.settings.max_items_per_store
