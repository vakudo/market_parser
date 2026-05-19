from __future__ import annotations

import asyncio

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from market_parser.models import ProductPrice
from market_parser.normalize import wb_units_to_kopecks
from market_parser.stores.base import BaseStoreAdapter, StoreMetadata


class WildberriesAdapter(BaseStoreAdapter):
    metadata = StoreMetadata(
        slug="wildberries",
        name="Вайлдберриз",
        channel="Е-ком",
        category_url="https://www.wildberries.ru/catalog/0/search.aspx?search=детское%20питание",
    )

    api_url = "https://search.wb.ru/exactmatch/ru/common/v18/search"

    async def fetch_category(self, limit: int | None = None) -> list[ProductPrice]:
        effective_limit = self.effective_limit(limit)
        products: list[ProductPrice] = []
        page = 1
        async with httpx.AsyncClient(
            timeout=self.settings.request_timeout_seconds,
            headers={"User-Agent": self.settings.user_agent, "Accept-Language": "ru-RU,ru;q=0.9"},
        ) as client:
            while len(products) < effective_limit:
                try:
                    payload = await self._fetch_page(client, page)
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 429 and products:
                        break
                    raise
                page_products = payload.get("products") or []
                if not page_products:
                    break
                for item in page_products:
                    products.append(self._product_from_api_item(item))
                    if len(products) >= effective_limit:
                        break
                page += 1
                await asyncio.sleep(self.settings.page_delay_seconds)
        return products

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def _fetch_page(self, client: httpx.AsyncClient, page: int) -> dict:
        response = await client.get(
            self.api_url,
            params={
                "ab_testing": "false",
                "appType": "1",
                "curr": "rub",
                "dest": "-1257786",
                "query": "детское питание",
                "resultset": "catalog",
                "sort": "popular",
                "spp": "30",
                "suppressSpellcheck": "false",
                "page": str(page),
            },
        )
        response.raise_for_status()
        return response.json()

    def _product_from_api_item(self, item: dict) -> ProductPrice:
        item_id = str(item.get("id") or "")
        sizes = item.get("sizes") or []
        price_data = {}
        if sizes:
            price_data = sizes[0].get("price") or {}
        regular = wb_units_to_kopecks(price_data.get("basic"))
        promo = wb_units_to_kopecks(price_data.get("product"))
        if regular is not None and promo is not None and regular <= promo:
            regular, promo = promo, None
        elif regular is None:
            regular, promo = promo, None

        brand = str(item.get("brand") or "").strip()
        name = str(item.get("name") or "").strip()
        return ProductPrice(
            store_slug=self.metadata.slug,
            store_name=self.metadata.name,
            category=self.settings.category_name,
            brand=brand,
            product_name=name,
            product_url=f"https://www.wildberries.ru/catalog/{item_id}/detail.aspx",
            product_id=item_id,
            regular_price_kopecks=regular,
            promo_price_kopecks=promo,
            availability="in_stock" if item.get("totalQuantity", 0) else "unknown",
            raw={"supplier": item.get("supplier"), "entity": item.get("entity")},
        )
