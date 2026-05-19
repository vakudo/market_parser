from __future__ import annotations

from market_parser.stores.base import StoreMetadata
from market_parser.stores.browser import fetch_rendered_html
from market_parser.stores.generic_browser import GenericBrowserStoreAdapter
from market_parser.stores.html_extractors import parse_vprok_cards


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
        html = await self._fetch_html()
        return parse_vprok_cards(
            html,
            category=self.settings.category_name,
            limit=effective_limit,
        )

    async def _fetch_html(self) -> str:
        if not self.settings.use_browser:
            return await super()._fetch_html()
        return await fetch_rendered_html(self.metadata.category_url, self.settings, wait_ms=10000)
