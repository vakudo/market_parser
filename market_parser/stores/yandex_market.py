from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from market_parser.stores.base import StoreMetadata
from market_parser.stores.browser import fetch_rendered_html
from market_parser.stores.generic_browser import GenericBrowserStoreAdapter
from market_parser.stores.html_extractors import parse_yandex_market_cards


class YandexMarketAdapter(GenericBrowserStoreAdapter):
    metadata = StoreMetadata(
        slug="yandex_market",
        name="Яндекс.Маркет",
        channel="Е-ком",
        category_url="https://ngo.integration.market.yandex.ru/category/detskoye-i-diyeticheskoye-pitaniye",
    )
    product_href_patterns = (r"/product", r"/card/")

    async def fetch_category(self, limit: int | None = None):
        effective_limit = self.effective_limit(limit)
        products = []
        page = 1
        seen_ids: set[str] = set()
        while not self.limit_reached(len(products), effective_limit):
            html = await self._fetch_html_for_page(page)
            page_products = parse_yandex_market_cards(
                html,
                category=self.settings.category_name,
                limit=self.remaining_limit(len(products), effective_limit),
            )
            new_products = [
                product for product in page_products if product.product_id not in seen_ids
            ]
            if not new_products:
                break
            for product in new_products:
                seen_ids.add(product.product_id)
                products.append(product)
                if self.limit_reached(len(products), effective_limit):
                    break
            page += 1
            await self.polite_page_delay()
        return products

    async def _fetch_html_for_page(self, page: int) -> str:
        url = _with_page(self.metadata.category_url, page)
        if self.settings.use_browser:
            return await fetch_rendered_html(
                url,
                self.settings,
                wait_ms=1500,
                wait_selector='[data-baobab-name="productSnippet"]',
                scroll_steps=8,
            )
        return await self._fetch_url_http(url)

    async def _fetch_url_http(self, url: str) -> str:
        original_url = self.metadata.category_url
        self.metadata = StoreMetadata(
            slug=self.metadata.slug,
            name=self.metadata.name,
            channel=self.metadata.channel,
            category_url=url,
        )
        try:
            return await self._fetch_html()
        finally:
            self.metadata = StoreMetadata(
                slug=self.metadata.slug,
                name=self.metadata.name,
                channel=self.metadata.channel,
                category_url=original_url,
            )


def _with_page(url: str, page: int) -> str:
    if page <= 1:
        return url
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["page"] = str(page)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
