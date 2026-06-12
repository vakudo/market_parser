from __future__ import annotations

from market_parser.config import Settings
from market_parser.stores.base import BaseStoreAdapter
from market_parser.stores.detmir import DetmirAdapter
from market_parser.stores.komus import KomusAdapter
from market_parser.stores.magnit import MagnitAdapter
from market_parser.stores.metro import MetroAdapter
from market_parser.stores.ozon import OzonAdapter
from market_parser.stores.retail_sources import (
    AuchanAdapter,
    ChizhikAdapter,
    DixyAdapter,
    KrasnoeBeloeAdapter,
    LentaAdapter,
    PyaterochkaAdapter,
    VkusvillAdapter,
    YandexLavkaAdapter,
)
from market_parser.stores.stealth import (
    OnlinetradeStealthAdapter,
    PerekrestokStealthAdapter,
    SamokatStealthAdapter,
)
from market_parser.stores.vprok import VprokAdapter
from market_parser.stores.wildberries import WildberriesAdapter
from market_parser.stores.yandex_market import YandexMarketAdapter

STORE_ADAPTERS: dict[str, type[BaseStoreAdapter]] = {
    "auchan": AuchanAdapter,
    "chizhik": ChizhikAdapter,
    "detmir": DetmirAdapter,
    "dixy": DixyAdapter,
    "komus": KomusAdapter,
    "krasnoe_beloe": KrasnoeBeloeAdapter,
    "lenta": LentaAdapter,
    "magnit": MagnitAdapter,
    "metro": MetroAdapter,
    "onlinetrade": OnlinetradeStealthAdapter,
    "ozon": OzonAdapter,
    "perekrestok": PerekrestokStealthAdapter,
    "pyaterochka": PyaterochkaAdapter,
    "samokat": SamokatStealthAdapter,
    "vprok": VprokAdapter,
    "vkusvill": VkusvillAdapter,
    "wildberries": WildberriesAdapter,
    "yandex_lavka": YandexLavkaAdapter,
    "yandex_market": YandexMarketAdapter,
}


def create_adapter(slug: str, settings: Settings) -> BaseStoreAdapter:
    adapter_cls = STORE_ADAPTERS.get(slug)
    if adapter_cls is None:
        available = ", ".join(sorted(STORE_ADAPTERS))
        raise ValueError(f"Unknown store '{slug}'. Available: {available}")
    return adapter_cls(settings)


def list_stores() -> list[tuple[str, str]]:
    return [(slug, STORE_ADAPTERS[slug].metadata.name) for slug in sorted(STORE_ADAPTERS)]
