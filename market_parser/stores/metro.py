from __future__ import annotations

from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from market_parser.models import ProductPrice
from market_parser.normalize import guess_brand, normalize_text, parse_price_to_kopecks
from market_parser.stores.base import BaseStoreAdapter, StoreMetadata
from market_parser.stores.html_extractors import ensure_not_blocked


class MetroAdapter(BaseStoreAdapter):
    metadata = StoreMetadata(
        slug="metro",
        name="Метро (РЕАЛ)",
        channel="Федеральная сеть",
        category_url="https://online.metro-cc.ru/category/detskie-tovary/detskoe-pitanie",
    )

    async def fetch_category(self, limit: int | None = None) -> list[ProductPrice]:
        effective_limit = self.effective_limit(limit)
        products: list[ProductPrice] = []
        seen: set[str] = set()
        page = 1
        async with httpx.AsyncClient(
            timeout=self.settings.request_timeout_seconds,
            headers={"User-Agent": self.settings.user_agent, "Accept-Language": "ru-RU,ru;q=0.9"},
            follow_redirects=True,
        ) as client:
            while not self.limit_reached(len(products), effective_limit):
                html = await self._fetch_page(client, page)
                page_products = parse_metro_cards(
                    html,
                    category=self.settings.category_name,
                    limit=self.remaining_limit(len(products), effective_limit),
                )
                new_products = [
                    product for product in page_products if product.product_id not in seen
                ]
                if not new_products:
                    break
                for product in new_products:
                    seen.add(product.product_id)
                    products.append(product)
                    if self.limit_reached(len(products), effective_limit):
                        break
                page += 1
                await self.polite_page_delay()
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
            else f"{self.metadata.category_url}?page={page}"
        )
        response = await client.get(url)
        response.raise_for_status()
        return response.text


def parse_metro_cards(html: str, *, category: str, limit: int | None) -> list[ProductPrice]:
    ensure_not_blocked(html, "Метро (РЕАЛ)")
    soup = BeautifulSoup(html, "html.parser")
    products: list[ProductPrice] = []
    for card in soup.select(".product-card[data-sku]"):
        link = card.select_one('a.product-card-name[href*="/products/"]')
        if not link:
            link = card.select_one('a[href*="/products/"][title]')
        title = normalize_text((link.get("title") if link else "") or "")
        if not title:
            continue
        href = str(link.get("href") or "") if link else ""
        current = _metro_price(card.select_one(".product-unit-prices__actual"))
        old = _metro_price(card.select_one(".product-unit-prices__old"))
        regular = old or current
        promo = current if old and current else None
        product_id = normalize_text(str(card.get("data-sku") or ""))
        products.append(
            ProductPrice(
                store_slug="metro",
                store_name="Метро (РЕАЛ)",
                category=category,
                brand=guess_brand(title),
                product_name=title,
                product_url=urljoin("https://online.metro-cc.ru", href),
                product_id=product_id,
                regular_price_kopecks=regular,
                promo_price_kopecks=promo,
                availability="in_stock",
            )
        )
        if limit is not None and len(products) >= limit:
            break
    return products


def _metro_price(node) -> int | None:
    if node is None:
        return None
    rubles = node.select_one(".product-price__sum-rubles")
    penny = node.select_one(".product-price__sum-penny")
    if not rubles:
        return parse_price_to_kopecks(node.get_text(" ", strip=True))
    value = normalize_text(rubles.get_text("", strip=True))
    if penny:
        value += normalize_text(penny.get_text("", strip=True))
    return parse_price_to_kopecks(value)
