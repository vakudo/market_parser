from __future__ import annotations

from market_parser.stores.base import StoreMetadata
from market_parser.stores.generic_browser import GenericBrowserStoreAdapter
from market_parser.stores.html_extractors import parse_vprok_cards
from market_parser.stores.retail_sources import _camoufox_get_html


class VprokAdapter(GenericBrowserStoreAdapter):
    metadata = StoreMetadata(
        slug="vprok",
        name="Перекресток ВПРОК",
        channel="Е-ком",
        category_url="https://www.vprok.ru/catalog/1432/detskoe-pitanie",
    )
    product_href_patterns = (r"/product/", r"/catalog/")

    async def fetch_category(self, limit: int | None = None):
        effective_limit = self.effective_limit(limit)
        # vprok.ru challenges plain Playwright/Chromium; Camoufox passes it.
        html = await _camoufox_get_html(
            self.metadata.category_url,
            self.settings,
            wait_ms=9_000,
            scroll_steps=8,
            attempts=2,
        )
        return parse_vprok_cards(
            html,
            category=self.settings.category_name,
            limit=effective_limit,
        )
