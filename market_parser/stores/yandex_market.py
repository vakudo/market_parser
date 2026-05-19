from __future__ import annotations

from market_parser.stores.base import StoreMetadata
from market_parser.stores.generic_browser import GenericBrowserStoreAdapter


class YandexMarketAdapter(GenericBrowserStoreAdapter):
    metadata = StoreMetadata(
        slug="yandex_market",
        name="Яндекс.Маркет",
        channel="Е-ком",
        category_url="https://market.yandex.ru/search?text=детское%20питание",
    )
    product_href_patterns = (r"/product", r"/card/")
