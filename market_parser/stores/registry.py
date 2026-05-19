from __future__ import annotations

from market_parser.config import Settings
from market_parser.stores.base import BaseStoreAdapter
from market_parser.stores.detmir import DetmirAdapter
from market_parser.stores.ozon import OzonAdapter
from market_parser.stores.vprok import VprokAdapter
from market_parser.stores.wildberries import WildberriesAdapter
from market_parser.stores.yandex_market import YandexMarketAdapter

STORE_ADAPTERS: dict[str, type[BaseStoreAdapter]] = {
    "ozon": OzonAdapter,
    "wildberries": WildberriesAdapter,
    "detmir": DetmirAdapter,
    "vprok": VprokAdapter,
    "yandex_market": YandexMarketAdapter,
}


def create_adapter(slug: str, settings: Settings) -> BaseStoreAdapter:
    adapter_cls = STORE_ADAPTERS.get(slug)
    if adapter_cls is None:
        available = ", ".join(sorted(STORE_ADAPTERS))
        raise ValueError(f"Unknown store '{slug}'. Available: {available}")
    return adapter_cls(settings)


def list_stores() -> list[tuple[str, str]]:
    return [(slug, adapter.metadata.name) for slug, adapter in STORE_ADAPTERS.items()]
