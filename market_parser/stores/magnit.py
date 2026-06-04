from __future__ import annotations

from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from market_parser.models import ProductPrice
from market_parser.normalize import (
    extract_price_values,
    guess_brand,
    normalize_text,
)
from market_parser.stores.base import BaseStoreAdapter, StoreMetadata
from market_parser.stores.html_extractors import ensure_not_blocked


class MagnitAdapter(BaseStoreAdapter):
    metadata = StoreMetadata(
        slug="magnit",
        name="Магнит",
        channel="Федеральная сеть",
        category_url="https://magnit.ru/catalog/64423-testmmnemolochnoe_pitanie",
    )

    category_urls = (
        "https://magnit.ru/catalog/64423-testmmnemolochnoe_pitanie",
        "https://magnit.ru/catalog/64421-testmmmolochnoe_pitanie",
    )

    async def fetch_category(self, limit: int | None = None) -> list[ProductPrice]:
        effective_limit = self.effective_limit(limit)
        products: list[ProductPrice] = []
        seen: set[str] = set()
        async with httpx.AsyncClient(
            timeout=self.settings.request_timeout_seconds,
            headers={"User-Agent": self.settings.user_agent, "Accept-Language": "ru-RU,ru;q=0.9"},
            follow_redirects=True,
        ) as client:
            for category_url in self.category_urls:
                page = 1
                while not self.limit_reached(len(products), effective_limit):
                    html = await self._fetch_page(client, category_url, page)
                    page_products = parse_magnit_cards(
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
                if self.limit_reached(len(products), effective_limit):
                    break
        return products

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def _fetch_page(self, client: httpx.AsyncClient, category_url: str, page: int) -> str:
        url = category_url if page == 1 else f"{category_url}?page={page}"
        response = await client.get(url)
        response.raise_for_status()
        return response.text


def parse_magnit_cards(html: str, *, category: str, limit: int | None) -> list[ProductPrice]:
    ensure_not_blocked(html, "Магнит")
    soup = BeautifulSoup(html, "html.parser")
    products: list[ProductPrice] = []
    for article in soup.select("article.unit-catalog-product-preview"):
        link = article.select_one('a[href*="/product/"][title]')
        title = normalize_text((link.get("title") if link else "") or "")
        if not title:
            title_node = article.select_one(".unit-catalog-product-preview-title")
            title = normalize_text(title_node.get_text(" ", strip=True) if title_node else "")
        if not title:
            continue
        href = str(link.get("href") or "") if link else ""
        # Magnit shows the loyalty-card price as the main figure and the
        # without-card price as the struck-through "sale" figure.
        current = _magnit_price(article, ".unit-catalog-product-preview-prices__regular")
        old = _magnit_price(article, ".unit-catalog-product-preview-prices__sale")
        if old and current and old != current:
            regular, loyalty = old, current
        else:
            regular, loyalty = (current or old), None
        product_id = _magnit_id_from_href(href)
        products.append(
            ProductPrice(
                store_slug="magnit",
                store_name="Магнит",
                category=category,
                brand=guess_brand(title),
                product_name=title,
                product_url=urljoin("https://magnit.ru", href),
                product_id=product_id,
                regular_price_kopecks=regular,
                promo_price_kopecks=None,
                loyalty_price_kopecks=loyalty,
                rating=_magnit_rating(article),
                availability="in_stock",
            )
        )
        if limit is not None and len(products) >= limit:
            break
    return products


def _magnit_price(article, selector: str) -> int | None:
    el = article.select_one(selector)
    if el is None:
        return None
    values = extract_price_values(el.get_text(" ", strip=True))
    return values[0] if values else None


def _magnit_rating(article) -> float | None:
    el = article.select_one(".unit-catalog-product-preview-rating-score")
    if el is None:
        return None
    try:
        value = float(normalize_text(el.get_text(" ", strip=True)).replace(",", "."))
    except ValueError:
        return None
    return value if 0 < value <= 5 else None


def _magnit_id_from_href(href: str) -> str:
    path = href.split("?", 1)[0].strip("/")
    parts = path.split("/")
    return parts[-1] if parts else href
