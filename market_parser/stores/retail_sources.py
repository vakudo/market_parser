from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import quote, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from market_parser.models import ProductPrice
from market_parser.normalize import (
    choose_price_fields,
    extract_price_values,
    guess_brand,
    normalize_text,
    parse_price_to_kopecks,
)
from market_parser.stores.base import (
    BaseStoreAdapter,
    StoreAdapterError,
    StoreBlockedError,
    StoreMetadata,
    StoreRateLimitedError,
)
from market_parser.stores.browser import fetch_rendered_html
from market_parser.stores.html_extractors import ensure_not_blocked, parse_generic_product_cards

BABY_FOOD_MARKERS = (
    "детск",
    "дети",
    "ребен",
    "ребён",
    "мес",
    "агуш",
    "фрутонян",
    "пюре",
    "каша",
    "смесь",
    "молочк",
    "творож",
    "биолакт",
    "сок",
    "компот",
    "nemoloko",
    "nutrilon",
    "nan",
    "gerber",
    "semper",
    "kabrita",
)




class RetailSourceAdapter(BaseStoreAdapter):
    source_urls: tuple[str, ...] = ()
    product_href_patterns: tuple[str, ...] = (r"/product", r"/goods/", r"/good/", r"/cat/")
    wait_selector: str | None = None
    scroll_steps: int = 4
    browser_fallback: bool = True
    reader_fallback: bool = True
    paginate_browser: bool = False  # grocery sites: warm browser + ?page=N pagination
    warmup_url: str | None = None
    max_pages: int = 40

    async def fetch_category(self, limit: int | None = None) -> list[ProductPrice]:
        if self.paginate_browser:
            return await self._generic_browser_fetch(limit)
        effective_limit = self.effective_limit(limit)
        products: list[ProductPrice] = []
        for url in self.source_urls or (self.metadata.category_url,):
            try:
                products.extend(await self._fetch_source(url, effective_limit, len(products)))
            except StoreAdapterError:
                pass
            products = _dedupe(products)
            if self.limit_reached(len(products), effective_limit):
                break
            await self.polite_page_delay()

        return products[:effective_limit] if effective_limit is not None else products

    async def _generic_browser_fetch(self, limit: int | None) -> list[ProductPrice]:
        def parse(html: str, base_url: str) -> list[ProductPrice]:
            return parse_generic_product_cards(
                html,
                store_slug=self.metadata.slug,
                store_name=self.metadata.name,
                category=self.settings.category_name,
                base_url=base_url,
                product_href_patterns=self.product_href_patterns,
                limit=None,
            )

        return await self._paginate_browser_collect(
            parse,
            limit=limit,
            warmup_url=self.warmup_url,
            max_pages=self.max_pages,
        )

    async def _paginate_browser_collect(
        self,
        parse_fn,
        *,
        limit: int | None,
        warmup_url: str | None = None,
        max_pages: int = 40,
        delay_ms: int = 2_500,
        wait_ms: int = 4_500,
    ) -> list[ProductPrice]:
        """Drive ONE warmed-up Camoufox browser through ``?page=N`` for every
        source URL, parsing rendered cards with ``parse_fn(html, base_url)``.
        Stops a URL when a page adds nothing new (so it is safe even when the
        site ignores ``?page``), paces requests to dodge rate-based antibots,
        and keeps partial results if one gets blocked mid-way."""
        effective_limit = self.effective_limit(limit)
        products: list[ProductPrice] = []
        seen: set[str] = set()
        browser = None
        try:
            browser, page = await _camoufox_new_page(self.settings)
            if warmup_url:
                try:
                    await _render_on_page(page, warmup_url, wait_ms=3_500, scroll_steps=1)
                except Exception:
                    pass
            for url in self.source_urls or (self.metadata.category_url,):
                for page_num in range(1, max_pages + 1):
                    sep = "&" if "?" in url else "?"
                    page_url = url if page_num == 1 else f"{url}{sep}page={page_num}"
                    html = await _render_on_page(
                        page, page_url, wait_ms=wait_ms, scroll_steps=self.scroll_steps
                    )
                    try:
                        cards = parse_fn(html, page_url)
                    except StoreBlockedError:
                        if not products:
                            raise
                        return products
                    added = 0
                    for product in cards:
                        key = (product.store_slug, product.product_id or product.product_url)
                        if key in seen:
                            continue
                        seen.add(key)
                        products.append(product)
                        added += 1
                        if self.limit_reached(len(products), effective_limit):
                            return products
                    if added == 0:
                        break
                    await page.wait_for_timeout(delay_ms)
        finally:
            if browser is not None:
                await browser.close()
        return products

    async def _fetch_source(
        self,
        url: str,
        limit: int | None,
        current_count: int,
    ) -> list[ProductPrice]:
        remaining = self.remaining_limit(current_count, limit)
        try:
            html = await self._fetch_http(url)
            products = self._parse_html(html, url, remaining)
            if products:
                return products
        except Exception:
            pass

        if self.settings.use_browser and self.browser_fallback:
            try:
                html = await fetch_rendered_html(
                    url,
                    self.settings,
                    wait_ms=2500,
                    wait_selector=self.wait_selector,
                    scroll_steps=self.scroll_steps,
                )
                products = self._parse_html(html, url, remaining)
                if products:
                    return products
            except Exception:
                pass

        if self.reader_fallback:
            try:
                text = await self._fetch_reader(url)
                products = parse_markdown_products(
                    text,
                    store_slug=self.metadata.slug,
                    store_name=self.metadata.name,
                    category=self.settings.category_name,
                    base_url=url,
                    limit=remaining,
                )
                if products:
                    return products
            except Exception:
                pass
        return []

    async def _fetch_http(self, url: str) -> str:
        async with httpx.AsyncClient(
            timeout=self.settings.request_timeout_seconds,
            headers={
                "User-Agent": self.settings.user_agent,
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            follow_redirects=True,
        ) as client:
            response = await client.get(url)
            if response.status_code in {401, 403, 429}:
                raise StoreBlockedError(
                    f"{self.metadata.name}: blocked HTTP {response.status_code}"
                )
            response.raise_for_status()
            return response.text

    async def _fetch_reader(self, url: str) -> str:
        reader_url = _reader_url(url)
        async with httpx.AsyncClient(
            timeout=max(self.settings.request_timeout_seconds, 45.0),
            headers={"User-Agent": self.settings.user_agent},
            follow_redirects=True,
        ) as client:
            response = await client.get(reader_url)
            response.raise_for_status()
            text = response.text
            if "Warning: Target URL returned error" in text and "Markdown Content:" not in text:
                raise StoreBlockedError(f"{self.metadata.name}: reader target blocked")
            return text

    def _parse_html(self, html: str, base_url: str, limit: int | None) -> list[ProductPrice]:
        ensure_not_blocked(html, self.metadata.name)
        products = parse_generic_product_cards(
            html,
            store_slug=self.metadata.slug,
            store_name=self.metadata.name,
            category=self.settings.category_name,
            base_url=base_url,
            product_href_patterns=self.product_href_patterns,
            limit=limit,
        )
        if limit is not None and len(products) >= limit:
            return _dedupe(products)[:limit]

        remaining = None if limit is None else limit - len(products)
        products.extend(
            parse_embedded_json_products(
                html,
                store_slug=self.metadata.slug,
                store_name=self.metadata.name,
                category=self.settings.category_name,
                base_url=base_url,
                limit=remaining,
            )
        )
        products = _dedupe(products)
        if limit is not None and len(products) >= limit:
            return products[:limit]

        remaining = None if limit is None else limit - len(products)
        products.extend(
            parse_markdown_products(
                html_to_text(html),
                store_slug=self.metadata.slug,
                store_name=self.metadata.name,
                category=self.settings.category_name,
                base_url=base_url,
                limit=remaining,
            )
        )
        products = _dedupe(products)
        return products[:limit] if limit is not None else products

def parse_embedded_json_products(
    html: str,
    *,
    store_slug: str,
    store_name: str,
    category: str,
    base_url: str,
    limit: int | None,
) -> list[ProductPrice]:
    soup = BeautifulSoup(html, "html.parser")
    products: list[ProductPrice] = []
    for script in soup.find_all("script"):
        raw = script.string or script.get_text()
        if not raw or not _looks_like_product_json(raw):
            continue
        for candidate in _json_candidates(raw):
            for product in _iter_product_dicts(candidate):
                parsed = _product_from_mapping(
                    product,
                    store_slug=store_slug,
                    store_name=store_name,
                    category=category,
                    base_url=base_url,
                )
                if parsed is None:
                    continue
                products.append(parsed)
                products = _dedupe(products)
                if limit is not None and len(products) >= limit:
                    return products[:limit]
    return _dedupe(products)


def parse_markdown_products(
    text: str,
    *,
    store_slug: str,
    store_name: str,
    category: str,
    base_url: str,
    limit: int | None,
) -> list[ProductPrice]:
    lines = [normalize_text(line) for line in text.splitlines()]
    lines = [line for line in lines if line]
    products: list[ProductPrice] = []

    for index, line in enumerate(lines):
        title, href = _candidate_from_line(line)
        if not title or not _is_baby_food_title(title):
            continue
        price_lines = [
            nearby
            for nearby in lines[index : index + 6]
            if "₽" in nearby or "руб" in nearby.lower()
        ]
        price_text = " ".join(price_lines)
        if not price_text:
            continue
        regular, promo, loyalty = choose_price_fields(
            extract_price_values(price_text),
            loyalty_context=("карт" in price_text.lower()),
        )
        if regular is None and promo is None and loyalty is None:
            continue
        product_url = urljoin(base_url, href or "")
        products.append(
            ProductPrice(
                store_slug=store_slug,
                store_name=store_name,
                category=category,
                brand=guess_brand(title),
                product_name=title,
                product_url=product_url or base_url,
                product_id=_id_from_url(product_url or title),
                regular_price_kopecks=regular,
                promo_price_kopecks=promo,
                loyalty_price_kopecks=loyalty,
                availability="unknown",
            )
        )
        products = _dedupe(products)
        if limit is not None and len(products) >= limit:
            break
    return products


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for node in soup(["script", "style", "noscript"]):
        node.decompose()
    return soup.get_text("\n", strip=True)


def _product_from_mapping(
    value: dict,
    *,
    store_slug: str,
    store_name: str,
    category: str,
    base_url: str,
) -> ProductPrice | None:
    title = normalize_text(
        str(
            value.get("name")
            or value.get("title")
            or value.get("productName")
            or value.get("displayName")
            or ""
        )
    )
    if not title or not _is_baby_food_title(title):
        return None

    price_values = _price_values_from_mapping(value)
    regular, promo, loyalty = choose_price_fields(
        price_values,
        loyalty_context=("card" in json.dumps(value, ensure_ascii=False).lower()),
    )
    url = (
        value.get("url")
        or value.get("href")
        or value.get("link")
        or value.get("webUrl")
        or value.get("canonicalUrl")
        or ""
    )
    product_id = str(
        value.get("id")
        or value.get("productId")
        or value.get("skuId")
        or value.get("plu")
        or value.get("code")
        or _id_from_url(str(url or title))
    )
    return ProductPrice(
        store_slug=store_slug,
        store_name=store_name,
        category=category,
        brand=normalize_text(str(value.get("brand") or value.get("brandName") or ""))
        or guess_brand(title),
        product_name=title,
        product_url=urljoin(base_url, str(url or "")),
        product_id=product_id,
        regular_price_kopecks=regular,
        promo_price_kopecks=promo,
        loyalty_price_kopecks=loyalty,
        availability="in_stock" if value.get("available") is True else "unknown",
    )


def _price_values_from_mapping(value: dict) -> list[int]:
    prices: list[int] = []
    for key, item in value.items():
        lowered = str(key).lower()
        if any(marker in lowered for marker in ("price", "cost", "amount", "regular", "promo")):
            if isinstance(item, dict):
                prices.extend(_price_values_from_mapping(item))
            elif isinstance(item, list):
                for child in item:
                    if isinstance(child, dict):
                        prices.extend(_price_values_from_mapping(child))
                    else:
                        parsed = parse_price_to_kopecks(child)
                        if parsed:
                            prices.append(parsed)
            else:
                parsed = parse_price_to_kopecks(item)
                if parsed:
                    prices.append(parsed)
    return [price for price in dict.fromkeys(prices) if 100 <= price <= 1_000_000]


def _iter_product_dicts(value):
    if isinstance(value, dict):
        if (
            (value.get("name") or value.get("title") or value.get("productName"))
            and _price_values_from_mapping(value)
        ):
            yield value
        for child in value.values():
            yield from _iter_product_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_product_dicts(child)


def _json_candidates(raw: str):
    raw = raw.strip()
    if raw.startswith("{") or raw.startswith("["):
        try:
            yield json.loads(raw)
            return
        except json.JSONDecodeError:
            pass

    decoder = json.JSONDecoder()
    offset = 0
    while True:
        start_candidates = [
            pos for pos in (raw.find("{", offset), raw.find("[", offset)) if pos >= 0
        ]
        if not start_candidates:
            return
        start = min(start_candidates)
        try:
            value, end = decoder.raw_decode(raw[start:])
        except json.JSONDecodeError:
            offset = start + 1
            continue
        yield value
        offset = start + max(end, 1)


def _looks_like_product_json(raw: str) -> bool:
    lowered = raw.lower()
    return any(marker in lowered for marker in ("product", "price", "sku", "offers", "товар"))


def _candidate_from_line(line: str) -> tuple[str, str]:
    if line.startswith("Title:") or line.startswith("URL Source:"):
        return "", ""
    line = re.sub(r"^#+\s*", "", line)
    line = re.sub(r"^\*\s*", "", line)
    link = re.search(r"\[([^\]]{5,220})\]\(([^)]+)\)", line)
    href = ""
    if link:
        title = link.group(1)
        href = link.group(2)
    else:
        title = line
    title = re.split(r"\s+(?:купить|цена|отзывы|в корзину)\b", title, flags=re.I)[0]
    title = normalize_text(re.sub(r"[*_`]+", "", title).strip(" -–|"))
    if len(title) < 8 or len(title) > 220:
        return "", ""
    lowered = title.casefold()
    if title[0].isdigit() or "₽" in title or "руб" in lowered:
        return "", ""
    if "ассортимент" in lowered or lowered in {"детское питание", "каталог"}:
        return "", ""
    return title, href


def _is_baby_food_title(title: str) -> bool:
    folded = normalize_text(title).casefold()
    if not folded:
        return False
    return any(marker in folded for marker in BABY_FOOD_MARKERS)


def _id_from_url(value: str) -> str:
    numbers = re.findall(r"\d{4,}", value)
    if numbers:
        return numbers[-1]
    slug = urlsplit(value).path.strip("/").split("/")[-1]
    return slug or normalize_text(value).casefold()[:80]


def _reader_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme == "https":
        return "https://r.jina.ai/http://" + parts.netloc + parts.path + (
            f"?{parts.query}" if parts.query else ""
        )
    if parts.scheme == "http":
        return "https://r.jina.ai/http://" + parts.netloc + parts.path + (
            f"?{parts.query}" if parts.query else ""
        )
    return "https://r.jina.ai/http://" + quote(url, safe="")


def _dedupe(products: Iterable[ProductPrice]) -> list[ProductPrice]:
    seen: set[tuple[str, str]] = set()
    result: list[ProductPrice] = []
    for product in products:
        key = (product.store_slug, product.product_id or product.product_url)
        if key in seen:
            continue
        seen.add(key)
        result.append(product)
    return result


def _camoufox_launch_kwargs(settings, *, block_images: bool = True) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "headless": settings.browser_headless,
        "block_images": block_images,
        "i_know_what_im_doing": True,
    }
    if settings.browser_proxy_server:
        proxy: dict[str, str] = {"server": settings.browser_proxy_server}
        if settings.browser_proxy_username:
            proxy["username"] = settings.browser_proxy_username
        if settings.browser_proxy_password:
            proxy["password"] = settings.browser_proxy_password
        kwargs["proxy"] = proxy
    return kwargs


async def _camoufox_new_page(settings, *, block_images: bool = True):
    try:
        from camoufox.async_api import AsyncCamoufox
    except ImportError as exc:
        raise StoreAdapterError("Camoufox is required for this store") from exc

    browser_cm = AsyncCamoufox(**_camoufox_launch_kwargs(settings, block_images=block_images))
    browser = await browser_cm.start()
    page = await browser.new_page(
        locale="ru-RU",
        timezone_id=settings.timezone,
        viewport={"width": 1365, "height": 900},
    )
    return browser, page


async def _camoufox_get_json(page, url: str, *, store_name: str) -> Any:
    response = await page.goto(url, wait_until="networkidle", timeout=60_000)
    status = response.status if response else None
    text = normalize_text(await page.locator("body").inner_text(timeout=10_000))
    if status in {401, 403}:
        raise StoreBlockedError(f"{store_name}: blocked HTTP {status}")
    if status == 429 or "rate limit" in text.casefold():
        raise StoreRateLimitedError(f"{store_name}: rate limited")
    if not text:
        raise StoreAdapterError(f"{store_name}: empty API response")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        ensure_not_blocked(text, store_name)
        raise StoreAdapterError(f"{store_name}: API returned non-JSON response") from exc


async def _camoufox_get_html(
    url: str,
    settings,
    *,
    wait_ms: int = 8_000,
    scroll_steps: int = 6,
    attempts: int = 2,
) -> str:
    browser = None
    try:
        browser, page = await _camoufox_new_page(settings)
        last_html = ""
        for _ in range(attempts):
            await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            await page.wait_for_timeout(wait_ms)
            try:
                await page.wait_for_load_state("networkidle", timeout=10_000)
            except Exception:
                pass
            for _ in range(scroll_steps):
                await page.mouse.wheel(0, 3000)
                await page.wait_for_timeout(700)
            last_html = await page.content()
            if "__qrator/qauth.js" not in last_html:
                return last_html
        return last_html
    finally:
        if browser is not None:
            await browser.close()


async def _render_on_page(
    page,
    url: str,
    *,
    wait_ms: int = 4_000,
    scroll_steps: int = 3,
) -> str:
    """Navigate an already-open (warmed-up) page and return its HTML, retrying
    once if an antibot interstitial is shown."""
    last_html = ""
    for _ in range(2):
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_timeout(wait_ms)
        for _ in range(scroll_steps):
            await page.mouse.wheel(0, 3000)
            await page.wait_for_timeout(700)
        last_html = await page.content()
        if "__qrator/qauth.js" not in last_html:
            return last_html
        await page.wait_for_timeout(3_000)
    return last_html


def parse_chizhik_products_payload(
    payload: dict[str, Any],
    *,
    store_name: str,
    category: str,
) -> list[ProductPrice]:
    products: list[ProductPrice] = []
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        title = normalize_text(str(item.get("title") or ""))
        if not title:
            continue
        price = parse_price_to_kopecks(item.get("price"))
        old_price = parse_price_to_kopecks(item.get("old_price"))
        product_id = str(item.get("id") or item.get("plu") or _id_from_url(title))
        products.append(
            ProductPrice(
                store_slug="chizhik",
                store_name=store_name,
                category=category,
                brand=guess_brand(title),
                product_name=title,
                product_url=(
                    "https://app.chizhik.club/api/v1/catalog/unauthorized/products/"
                    f"{product_id}/"
                ),
                product_id=product_id,
                regular_price_kopecks=old_price or price,
                promo_price_kopecks=price if old_price and price else None,
                availability="in_stock" if item.get("is_inout") is not False else "unknown",
                raw={"source": "api", "plu": item.get("plu")},
            )
        )
    return _dedupe(products)


def parse_pyaterochka_search_payload(
    payload: dict[str, Any],
    *,
    store_name: str,
    category: str,
) -> list[ProductPrice]:
    products: list[ProductPrice] = []
    for item in payload.get("products") or []:
        if not isinstance(item, dict):
            continue
        title = normalize_text(str(item.get("name") or item.get("title") or ""))
        if not title or not _is_baby_food_title(title):
            continue
        product_id = str(item.get("plu") or item.get("id") or _id_from_url(title))
        prices = _price_values_from_mapping(item)
        regular, promo, loyalty = choose_price_fields(
            prices,
            loyalty_context=("card" in json.dumps(item, ensure_ascii=False).casefold()),
        )
        slug = normalize_text(str(item.get("slug") or ""))
        url = (
            f"https://5ka.ru/product/{slug}--{product_id}/"
            if slug
            else f"https://5ka.ru/product/{product_id}/"
        )
        products.append(
            ProductPrice(
                store_slug="pyaterochka",
                store_name=store_name,
                category=category,
                brand=normalize_text(str(item.get("brand_name") or item.get("brand") or ""))
                or guess_brand(title),
                product_name=title,
                product_url=url,
                product_id=product_id,
                regular_price_kopecks=regular,
                promo_price_kopecks=promo,
                loyalty_price_kopecks=loyalty,
                availability="in_stock" if item.get("available") is not False else "unknown",
                raw={"source": "api"},
            )
        )
    return _dedupe(products)


def _auchan_price_value(node) -> int | None:
    if node is None:
        return None
    values = extract_price_values(node.get_text(" ", strip=True))
    return values[0] if values else None


def parse_auchan_cards(
    html: str,
    *,
    store_name: str,
    category: str,
    base_url: str,
    limit: int | None,
    filter_baby: bool = True,
) -> list[ProductPrice]:
    ensure_not_blocked(html, store_name)
    soup = BeautifulSoup(html, "html.parser")
    products: list[ProductPrice] = []
    for card in soup.select('div[class*="productCard__"]'):
        title_link = card.select_one('a[class*="productCardContentPanel_link"][href*="/product/"]')
        if title_link is None:
            title_link = card.select_one('a[href*="/product/"]')
        if title_link is None:
            continue
        title = normalize_text(title_link.get("title") or title_link.get_text(" ", strip=True))
        if not title or (filter_baby and not _is_baby_food_title(title)):
            continue
        href = str(title_link.get("href") or "")
        # Target the dedicated price elements — reading the whole card text glues
        # several numbers (price, old price, price/kg) into one bogus value.
        current = _auchan_price_value(
            card.select_one('[class*="styles_price__"]:not([class*="oldPrice"])')
        )
        old = _auchan_price_value(card.select_one('[class*="oldPrice"]'))
        regular = old or current
        promo = current if (old and current and current != old) else None
        loyalty = None
        if regular is None:
            continue
        products.append(
            ProductPrice(
                store_slug="auchan",
                store_name=store_name,
                category=category,
                brand=guess_brand(title),
                product_name=title,
                product_url=urljoin(base_url, href),
                product_id=_id_from_url(href),
                regular_price_kopecks=regular,
                promo_price_kopecks=promo,
                loyalty_price_kopecks=loyalty,
                availability="in_stock",
                raw={"source": "browser_html"},
            )
        )
        products = _dedupe(products)
        if limit is not None and len(products) >= limit:
            break
    return products[:limit] if limit is not None else products


def parse_lenta_cards(
    html: str,
    *,
    store_name: str,
    category: str,
    base_url: str,
    limit: int | None,
    filter_baby: bool = True,
) -> list[ProductPrice]:
    ensure_not_blocked(html, store_name)
    soup = BeautifulSoup(html, "html.parser")
    products: list[ProductPrice] = []
    for card in soup.select("lu-product-card"):
        link = card.select_one('a[automation-id="productCard"][href*="/product/"]')
        if link is None:
            continue
        name_node = card.select_one('[automation-id="product-names"]')
        package_node = card.select_one(".card-name_package")
        title = normalize_text(
            " ".join(
                part
                for part in (
                    name_node.get_text(" ", strip=True) if name_node else "",
                    package_node.get_text(" ", strip=True) if package_node else "",
                )
                if part
            )
        )
        if not title or (filter_baby and not _is_baby_food_title(title)):
            continue
        current_prices = extract_price_values(
            card.select_one(".main-price").get_text(" ", strip=True)
            if card.select_one(".main-price")
            else ""
        )
        old_prices = extract_price_values(
            card.select_one(".old-price-product").get_text(" ", strip=True)
            if card.select_one(".old-price-product")
            else ""
        )
        current = current_prices[0] if current_prices else None
        old = old_prices[0] if old_prices else None
        card_text = normalize_text(card.get_text(" ", strip=True))
        if "картой" in card_text.casefold():
            regular = old or current
            promo = None
            loyalty = current if old else None
        else:
            regular = old or current
            promo = current if old and current else None
            loyalty = None
        href = str(link.get("href") or "")
        products.append(
            ProductPrice(
                store_slug="lenta",
                store_name=store_name,
                category=category,
                brand=guess_brand(title),
                product_name=title,
                product_url=urljoin(base_url, href),
                product_id=_id_from_url(href),
                regular_price_kopecks=regular,
                promo_price_kopecks=promo,
                loyalty_price_kopecks=loyalty,
                availability="in_stock",
                raw={"source": "browser_html"},
            )
        )
        products = _dedupe(products)
        if limit is not None and len(products) >= limit:
            break
    return products[:limit] if limit is not None else products


class PyaterochkaAdapter(RetailSourceAdapter):
    metadata = StoreMetadata(
        slug="pyaterochka",
        name="Пятёрочка",
        channel="Федеральная сеть",
        category_url="https://5ka.ru/product/pyure-fruktovoe-agusha-detskoe-banan-s-6-mesyatsev--3606439/",
    )
    browser_fallback = False
    reader_fallback = False
    source_urls = (
        "https://5ka.ru/product/pyure-fruktovoe-agusha-detskoe-banan-s-6-mesyatsev--3606439/",
        "https://5ka.ru/product/kasha-molochnaya-detskaya-agusha-ovsyanka-malina-s--3667323/",
        "https://5ka.ru/product/pyure-fruktovomolochnoe-agusha-yablokotvorogpechen--3641227/",
    )
    product_href_patterns = (r"/product/",)
    search_terms = (
        "детское питание",
        "агуша",
        "фрутоняня",
        "пюре детское",
        "каша детская",
        "смесь детская",
        "сок детский",
        "молоко детское",
        "творожок детский",
    )
    store_coordinates = (
        (37.6328, 55.7426),
        (37.4146, 55.6981),
        (30.3159, 59.9391),
    )

    async def fetch_category(self, limit: int | None = None) -> list[ProductPrice]:
        effective_limit = self.effective_limit(limit)
        browser = None
        try:
            browser, page = await _camoufox_new_page(self.settings)
            store_id = await self._find_store_id(page)
            products: list[ProductPrice] = []
            try:
                for index, term in enumerate(self.search_terms):
                    payload = await self._search_term(page, store_id, term)
                    if isinstance(payload, dict):
                        products.extend(
                            parse_pyaterochka_search_payload(
                                payload,
                                store_name=self.metadata.name,
                                category=self.settings.category_name,
                            )
                        )
                        products = _dedupe(products)
                    if self.limit_reached(len(products), effective_limit):
                        break
                    if index + 1 < len(self.search_terms):
                        await page.wait_for_timeout(8_000)
            except StoreRateLimitedError:
                # Keep whatever was collected before the rate limit kicked in.
                if not products:
                    raise
            return products[:effective_limit] if effective_limit is not None else products
        finally:
            if browser is not None:
                await browser.close()

    async def _search_term(self, page, store_id: str, term: str):
        url = (
            "https://5d.5ka.ru/api/catalog/v3/stores/"
            f"{store_id}/search?mode=store&include_restrict=true"
            f"&q={quote(term)}&limit=50"
        )
        for attempt in range(4):
            try:
                return await _camoufox_get_json(page, url, store_name=self.metadata.name)
            except StoreRateLimitedError:
                if attempt == 3:
                    raise
                await page.wait_for_timeout(15_000 * (attempt + 1))

    async def _find_store_id(self, page) -> str:
        for longitude, latitude in self.store_coordinates:
            payload = await _camoufox_get_json(
                page,
                (
                    "https://5d.5ka.ru/api/orders/v1/orders/stores/"
                    f"?lon={longitude}&lat={latitude}"
                ),
                store_name=self.metadata.name,
            )
            if isinstance(payload, dict) and payload.get("sap_code"):
                return str(payload["sap_code"])
            await page.wait_for_timeout(6_500)
        return "J577"



class ChizhikAdapter(RetailSourceAdapter):
    metadata = StoreMetadata(
        slug="chizhik",
        name="Чижик",
        channel="Дискаунтер",
        category_url="https://chizhik.club/catalog/",
    )
    source_urls = ("https://chizhik.club/catalog/", "https://chizhik.club/")
    browser_fallback = False
    reader_fallback = False
    product_href_patterns = (r"/product", r"/catalog")
    category_ids = (10, 11, 13, 15, 16)

    async def fetch_category(self, limit: int | None = None) -> list[ProductPrice]:
        effective_limit = self.effective_limit(limit)
        browser = None
        try:
            browser, page = await _camoufox_new_page(self.settings)
            products: list[ProductPrice] = []
            for category_id in self.category_ids:
                page_num = 1
                while True:
                    payload = await _camoufox_get_json(
                        page,
                        (
                            "https://app.chizhik.club/api/v1/catalog/unauthorized/"
                            f"products/?page={page_num}&category_id={category_id}"
                        ),
                        store_name=self.metadata.name,
                    )
                    if not isinstance(payload, dict):
                        break
                    products.extend(
                        parse_chizhik_products_payload(
                            payload,
                            store_name=self.metadata.name,
                            category=self.settings.category_name,
                        )
                    )
                    products = _dedupe(products)
                    if self.limit_reached(len(products), effective_limit):
                        return products[:effective_limit]
                    next_page = payload.get("next")
                    if not next_page:
                        break
                    page_num = int(next_page)
            return products[:effective_limit] if effective_limit is not None else products
        finally:
            if browser is not None:
                await browser.close()



class AuchanAdapter(RetailSourceAdapter):
    metadata = StoreMetadata(
        slug="auchan",
        name="Ашан",
        channel="Федеральная сеть",
        category_url="https://www.auchan.ru/catalog/vse-dlya-detey/detskoe-pitanie/",
    )
    source_urls = (
        "https://www.auchan.ru/catalog/vse-dlya-detey/detskoe-pitanie/",
        "https://www.auchan.ru/catalog/vse-dlya-detey/detskoe-pitanie/suhie-smesi/",
        "https://www.auchan.ru/catalog/vse-dlya-detey/detskoe-pitanie/pyure/",
    )
    browser_fallback = False
    reader_fallback = False
    product_href_patterns = (r"/product/", r"/catalog/")
    scroll_steps = 6
    max_pages = 40

    async def fetch_category(self, limit: int | None = None) -> list[ProductPrice]:
        def parse(html: str, base_url: str) -> list[ProductPrice]:
            return parse_auchan_cards(
                html,
                store_name=self.metadata.name,
                category=self.settings.category_name,
                base_url=base_url,
                limit=None,
                filter_baby=False,
            )

        return await self._paginate_browser_collect(
            parse,
            limit=limit,
            warmup_url="https://www.auchan.ru/",
            max_pages=self.max_pages,
        )



class DixyAdapter(RetailSourceAdapter):
    metadata = StoreMetadata(
        slug="dixy",
        name="Дикси",
        channel="Федеральная сеть",
        category_url="https://dixy.ru/catalog/detskoe-pitanie/",
    )
    source_urls = (
        "https://dixy.ru/catalog/detskoe-pitanie/",
        "https://dixy.ru/catalog/detskoe-pitanie/kashi/",
    )
    browser_fallback = False
    reader_fallback = False
    product_href_patterns = (r"/product/", r"/catalog/")
    paginate_browser = True
    warmup_url = "https://dixy.ru/"


class KrasnoeBeloeAdapter(RetailSourceAdapter):
    metadata = StoreMetadata(
        slug="krasnoe_beloe",
        name="Красное и Белое",
        channel="Федеральная сеть",
        category_url="https://krasnoeibeloe.ru/catalog/moloko_syry_yaytso/detskoe_pitanie_1/",
    )
    source_urls = (
        "https://krasnoeibeloe.ru/catalog/moloko_syry_yaytso/detskoe_pitanie_1/",
    )
    product_href_patterns = (r"/catalog/.+/",)
    paginate_browser = True
    warmup_url = "https://krasnoeibeloe.ru/"


class LentaAdapter(RetailSourceAdapter):
    metadata = StoreMetadata(
        slug="lenta",
        name="Лента",
        channel="Федеральная сеть",
        category_url="https://lenta.com/catalog/detskoe-pitanie-19327/",
    )
    source_urls = (
        "https://lenta.com/catalog/detskoe-pitanie-19327/",
        "https://lenta.com/catalog/detskie-smesi-19328/",
        "https://lenta.com/catalog/fruktovoe-pyure-19335/",
        "https://lenta.com/catalog/myasnoe-i-ovoshchnoe-pyure-19336/",
        "https://lenta.com/catalog/kashi-19342/",
    )
    browser_fallback = False
    reader_fallback = False
    product_href_patterns = (r"/product/", r"/catalog/")
    max_pages = 40  # lenta paginates ~80 cards/page via ?page=N

    async def fetch_category(self, limit: int | None = None) -> list[ProductPrice]:
        effective_limit = self.effective_limit(limit)
        products: list[ProductPrice] = []
        seen: set[str] = set()
        browser = None
        try:
            # One warmed-up browser for the whole run: Lenta's Qrator antibot
            # challenges cold sessions, so reuse cookies across all pages.
            browser, page = await _camoufox_new_page(self.settings)
            # Warm up on the homepage first so Qrator issues a clean session cookie.
            try:
                await _render_on_page(page, "https://lenta.com/", wait_ms=4_000, scroll_steps=1)
            except Exception:
                pass
            for url in self.source_urls:
                for page_num in range(1, self.max_pages + 1):
                    page_url = url if page_num == 1 else f"{url}?page={page_num}"
                    html = await _render_on_page(page, page_url, wait_ms=4_500, scroll_steps=3)
                    try:
                        cards = parse_lenta_cards(
                            html,
                            store_name=self.metadata.name,
                            category=self.settings.category_name,
                            base_url=page_url,
                            limit=None,
                            filter_baby=False,
                        )
                    except StoreBlockedError:
                        # Rate-flagged by Qrator — keep what we already have.
                        if not products:
                            raise
                        return products
                    added = 0
                    for product in cards:
                        key = product.product_id or product.product_url
                        if key in seen:
                            continue
                        seen.add(key)
                        products.append(product)
                        added += 1
                        if self.limit_reached(len(products), effective_limit):
                            return products
                    if added == 0:
                        break
                    await page.wait_for_timeout(2_500)
        finally:
            if browser is not None:
                await browser.close()
        return products



class VkusvillAdapter(RetailSourceAdapter):
    metadata = StoreMetadata(
        slug="vkusvill",
        name="ВкусВилл",
        channel="Федеральная сеть",
        category_url="https://vkusvill.ru/goods/tovary-dlya-detey/detskoe-molochnoe-pitanie/",
    )
    source_urls = (
        "https://vkusvill.ru/goods/tovary-dlya-detey/detskoe-molochnoe-pitanie/",
        "https://vkusvill.ru/goods/krupy-makarony-muka/detskie-supchiki-makarony-i-kashi/",
        "https://vkusvill.ru/goods/napitki/detskie-napitki/",
    )
    product_href_patterns = (r"/goods/",)
    paginate_browser = True
    warmup_url = "https://vkusvill.ru/"


class OnlinetradeAdapter(RetailSourceAdapter):
    auto_runnable = False  # collected manually via run_logs/cdp_ot_collect.py
    metadata = StoreMetadata(
        slug="onlinetrade",
        name="Онлайнтрейд.ру",
        channel="Е-ком",
        category_url="https://www.onlinetrade.ru/catalogue/detskoe_pyure-c1210/",
    )
    source_urls = (
        "https://www.onlinetrade.ru/catalogue/detskoe_pyure-c1210/",
        "https://www.onlinetrade.ru/catalogue/detskoe_pyure-c1210/frutonyanya/",
    )
    browser_fallback = False
    reader_fallback = False
    product_href_patterns = (r"/catalogue/.+-p\d+/", r"/catalogue/")


class SamokatAdapter(RetailSourceAdapter):
    auto_runnable = False  # collected manually via run_logs/cdp_collect.py
    metadata = StoreMetadata(
        slug="samokat",
        name="Самокат",
        channel="Е-ком",
        category_url="https://samokat.ru/",
    )
    source_urls = ("https://samokat.ru/",)
    browser_fallback = False
    reader_fallback = False
    product_href_patterns = (r"/product", r"/category")


class PerekrestokAdapter(RetailSourceAdapter):
    auto_runnable = False  # collected manually via run_logs/cdp_pk_collect.py
    metadata = StoreMetadata(
        slug="perekrestok",
        name="Перекрёсток",
        channel="Федеральная сеть",
        category_url="https://www.perekrestok.ru/cat/230/detskoe-pitanie",
    )
    source_urls = (
        "https://www.perekrestok.ru/cat/230/detskoe-pitanie",
        "https://promo.perekrestok.ru/cat/230/p/pure-fruktovoe-agusa-grusa-s-4-mesacev-115g-3001752",
        "https://promo.perekrestok.ru/cat/230/p/sok-detskij-agusa-abloko-mango-banan-s-makotu-500ml-4196500",
    )
    browser_fallback = False
    reader_fallback = False
    product_href_patterns = (r"/cat/\d+/p/", r"/product/")


class YandexLavkaAdapter(RetailSourceAdapter):
    metadata = StoreMetadata(
        slug="yandex_lavka",
        name="Яндекс.Лавка",
        channel="Е-ком",
        category_url="https://lavka.yandex.ru/compilations/1-пюре-фрутоняня-ассортимент",
    )
    source_urls = (
        "https://lavka.yandex.ru/compilations/1-пюре-фрутоняня-ассортимент",
        "https://lavka.yandex.ru/compilations/безлактозное-детское-питание",
    )
    product_href_patterns = (r"/good/", r"/compilations/")
