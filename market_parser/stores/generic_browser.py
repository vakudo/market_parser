from __future__ import annotations

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from market_parser.models import ProductPrice
from market_parser.stores.base import BaseStoreAdapter, StoreAdapterError, StoreMetadata
from market_parser.stores.browser import fetch_rendered_html
from market_parser.stores.html_extractors import parse_generic_product_cards


class GenericBrowserStoreAdapter(BaseStoreAdapter):
    metadata: StoreMetadata
    product_href_patterns: tuple[str, ...] = (r"/product",)

    async def fetch_category(self, limit: int | None = None) -> list[ProductPrice]:
        effective_limit = self.effective_limit(limit)
        html = await self._fetch_html()
        return parse_generic_product_cards(
            html,
            store_slug=self.metadata.slug,
            store_name=self.metadata.name,
            category=self.settings.category_name,
            base_url=self.metadata.category_url,
            product_href_patterns=self.product_href_patterns,
            limit=effective_limit,
        )

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError, StoreAdapterError)),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        stop=stop_after_attempt(2),
        reraise=True,
    )
    async def _fetch_html(self) -> str:
        try:
            async with httpx.AsyncClient(
                timeout=self.settings.request_timeout_seconds,
                headers={
                    "User-Agent": self.settings.user_agent,
                    "Accept-Language": "ru-RU,ru;q=0.9",
                },
                follow_redirects=True,
            ) as client:
                response = await client.get(self.metadata.category_url)
                response.raise_for_status()
                html = response.text
                if len(html) > 5000:
                    return html
        except httpx.HTTPError:
            if not self.settings.use_browser:
                raise

        if not self.settings.use_browser:
            raise StoreAdapterError(f"{self.metadata.name}: HTTP page did not contain enough data")
        return await fetch_rendered_html(self.metadata.category_url, self.settings)
