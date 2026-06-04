from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote, urljoin

from market_parser.models import ProductPrice
from market_parser.normalize import guess_brand, normalize_text, parse_price_to_kopecks
from market_parser.stores.base import (
    BaseStoreAdapter,
    StoreAdapterError,
    StoreMetadata,
)
from market_parser.stores.retail_sources import (
    _camoufox_get_json,
    _camoufox_new_page,
    _dedupe,
    _id_from_url,
)

OZON_ORIGIN = "https://www.ozon.ru"
COMPOSER_API = OZON_ORIGIN + "/api/composer-api.bx/page/json/v2?url="


class OzonAdapter(BaseStoreAdapter):
    """Ozon hides its catalog behind an antibot challenge, but the internal
    ``composer-api`` still returns product JSON once the browser context holds
    valid Ozon cookies. We warm those cookies up with a real Camoufox visit to
    the category page (it may show a captcha — that's fine, the cookies are set
    anyway) and then page through the JSON API."""

    metadata = StoreMetadata(
        slug="ozon",
        name="Озон",
        channel="Е-ком",
        category_url="https://www.ozon.ru/category/detskoe-pitanie-7030/",
    )
    category_path = "/category/detskoe-pitanie-7030/"
    max_pages = 5000  # Ozon paginates ~8 products/page; keep going until nextPage is None

    async def fetch_category(self, limit: int | None = None) -> list[ProductPrice]:
        effective_limit = self.effective_limit(limit)
        browser = None
        try:
            browser, page = await _camoufox_new_page(self.settings)
            await self._warm_up(page)

            products: list[ProductPrice] = []
            seen: set[str] = set()
            next_path: str | None = self.category_path
            empty_streak = 0
            for _ in range(self.max_pages):
                payload = await _camoufox_get_json(
                    page,
                    COMPOSER_API + quote(next_path, safe=""),
                    store_name=self.metadata.name,
                )
                added = 0
                for product in parse_ozon_widgets(
                    payload,
                    store_name=self.metadata.name,
                    category=self.settings.category_name,
                ):
                    key = product.product_id or product.product_url
                    if key in seen:
                        continue
                    seen.add(key)
                    products.append(product)
                    added += 1
                if self.limit_reached(len(products), effective_limit):
                    break
                # stop if several consecutive pages bring nothing new
                empty_streak = empty_streak + 1 if added == 0 else 0
                if empty_streak >= 3:
                    break
                next_path = _next_page_path(payload)
                if not next_path:
                    break
                await page.wait_for_timeout(700)

            if not products:
                raise StoreAdapterError(f"{self.metadata.name}: no products parsed")
            return products[:effective_limit] if effective_limit is not None else products
        finally:
            if browser is not None:
                await browser.close()

    async def _warm_up(self, page) -> None:
        try:
            await page.goto(
                OZON_ORIGIN + self.category_path,
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            await page.wait_for_timeout(6_000)
        except Exception:
            # A captcha/timeout here is expected — the cookies we need are
            # attached to the response regardless.
            pass


def parse_ozon_widgets(
    payload: Any,
    *,
    store_name: str,
    category: str,
) -> list[ProductPrice]:
    widget_states = payload.get("widgetStates") if isinstance(payload, dict) else None
    if not isinstance(widget_states, dict):
        return []
    products: list[ProductPrice] = []
    for key, raw in widget_states.items():
        lowered = key.lower()
        if "tile" not in lowered and "searchresult" not in lowered:
            continue
        state = _loads(raw)
        items = state.get("items") if isinstance(state, dict) else None
        if not isinstance(items, list):
            continue
        for item in items:
            product = _product_from_tile(item, store_name=store_name, category=category)
            if product is not None:
                products.append(product)
    return _dedupe(products)


def _next_page_path(payload: Any) -> str | None:
    widget_states = payload.get("widgetStates") if isinstance(payload, dict) else None
    if isinstance(widget_states, dict):
        for key, raw in widget_states.items():
            if "aginator" not in key:
                continue
            state = _loads(raw)
            next_page = state.get("nextPage") if isinstance(state, dict) else None
            if next_page:
                return str(next_page)
    next_page = payload.get("nextPage") if isinstance(payload, dict) else None
    return str(next_page) if next_page else None


def _product_from_tile(
    item: Any,
    *,
    store_name: str,
    category: str,
) -> ProductPrice | None:
    if not isinstance(item, dict):
        return None
    main_state = item.get("mainState")
    if not isinstance(main_state, list):
        return None
    title = _tile_title(main_state)
    if not title:
        return None
    regular, promo, loyalty = _tile_prices(main_state)
    if regular is None and promo is None and loyalty is None:
        return None
    rating = _tile_rating(main_state)

    link = ""
    action = item.get("action")
    if isinstance(action, dict):
        link = str(action.get("link") or "")
    path = link.split("?", 1)[0] or link
    product_id = str(item.get("sku") or item.get("id") or _id_from_url(path or title))
    return ProductPrice(
        store_slug="ozon",
        store_name=store_name,
        category=category,
        brand=guess_brand(title),
        product_name=title,
        product_url=urljoin(OZON_ORIGIN, path),
        product_id=product_id,
        regular_price_kopecks=regular,
        promo_price_kopecks=promo,
        loyalty_price_kopecks=loyalty,
        rating=rating,
        availability="in_stock",
        raw={"source": "composer-api"},
    )


def _tile_title(main_state: list) -> str:
    for entry in main_state:
        if not isinstance(entry, dict):
            continue
        text_ds = entry.get("textDS")
        if not isinstance(text_ds, dict):
            continue
        info = text_ds.get("testInfo") or {}
        if entry.get("id") == "name" or info.get("automatizationId") == "tile-name":
            text = text_ds.get("text")
            if text:
                return normalize_text(str(text))
    return ""


def _tile_rating(main_state: list) -> float | None:
    for entry in main_state:
        if not isinstance(entry, dict) or entry.get("type") != "labelListV2":
            continue
        label = entry.get("labelListV2")
        if not isinstance(label, dict):
            continue
        info = label.get("testInfo") or {}
        if info.get("automatizationId") != "tile-list-rating":
            continue
        for sub in label.get("items") or []:
            if not isinstance(sub, dict) or sub.get("type") != "text":
                continue
            rating = _parse_rating((sub.get("text") or {}).get("text"))
            if rating is not None:
                return rating
    return None


def _parse_rating(text: Any) -> float | None:
    if not text:
        return None
    try:
        value = float(normalize_text(str(text)).replace(",", "."))
    except ValueError:
        return None
    return value if 0 < value <= 5 else None


def _tile_prices(main_state: list) -> tuple[int | None, int | None, int | None]:
    for entry in main_state:
        if not isinstance(entry, dict) or entry.get("type") != "priceV2":
            continue
        price_v2 = entry.get("priceV2")
        if not isinstance(price_v2, dict):
            continue
        current = original = None
        for price in price_v2.get("price") or []:
            if not isinstance(price, dict):
                continue
            value = parse_price_to_kopecks(price.get("text"))
            if not value:
                continue
            style = price.get("textStyle")
            if style == "ORIGINAL_PRICE":
                original = value
            elif style == "PRICE":
                current = value
        if current is None and original is None:
            continue
        regular = original or current
        is_card = (price_v2.get("priceStyle") or {}).get("styleType") == "CARD_PRICE"
        if is_card:
            loyalty = current if current != regular else None
            return regular, None, loyalty
        promo = current if (original and current and current != original) else None
        return regular, promo, None
    return None, None, None


def _loads(raw: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
