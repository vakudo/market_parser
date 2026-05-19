from __future__ import annotations

from market_parser.stores.base import StoreMetadata
from market_parser.stores.generic_browser import GenericBrowserStoreAdapter


class OzonAdapter(GenericBrowserStoreAdapter):
    metadata = StoreMetadata(
        slug="ozon",
        name="Озон",
        channel="Е-ком",
        category_url="https://www.ozon.ru/category/detskoe-pitanie-7030/",
    )
    product_href_patterns = (r"/product/",)
