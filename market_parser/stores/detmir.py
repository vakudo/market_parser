from __future__ import annotations

import asyncio
import re
from urllib.parse import urljoin

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from market_parser.models import ProductPrice
from market_parser.stores.base import BaseStoreAdapter, StoreMetadata
from market_parser.stores.html_extractors import parse_detmir_cards


class DetmirAdapter(BaseStoreAdapter):
    metadata = StoreMetadata(
        slug="detmir",
        name="Детский мир",
        channel="Федеральная сеть",
        category_url="https://www.detmir.ru/catalog/index/name/baby_food_milk/",
    )

    async def fetch_category(self, limit: int | None = None) -> list[ProductPrice]:
        effective_limit = self.effective_limit(limit)
        products: list[ProductPrice] = []
        page = 1
        max_pages = 100
        async with httpx.AsyncClient(
            timeout=self.settings.request_timeout_seconds,
            headers={"User-Agent": self.settings.user_agent, "Accept-Language": "ru-RU,ru;q=0.9"},
            follow_redirects=True,
        ) as client:
            while len(products) < effective_limit and page <= max_pages:
                html = await self._fetch_page(client, page)
                page_items = parse_detmir_cards(
                    html,
                    category=self.settings.category_name,
                    limit=effective_limit - len(products),
                )
                if not page_items:
                    break
                new_items = [
                    item
                    for item in page_items
                    if item.product_id not in {product.product_id for product in products}
                ]
                if not new_items:
                    break
                products.extend(new_items)
                page += 1
                await asyncio.sleep(self.settings.page_delay_seconds)
        return products

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def _fetch_page(self, client: httpx.AsyncClient, page: int) -> str:
        url = (
            self.metadata.category_url
            if page == 1
            else urljoin(self.metadata.category_url, f"?page={page}")
        )
        response = await client.get(url)
        response.raise_for_status()
        return response.text

    @staticmethod
    def _has_next_page(html: str, page: int) -> bool:
        return bool(re.search(rf"[?&]page={page + 1}\b", html))
