from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote, urljoin

from market_parser.models import ProductPrice
from market_parser.normalize import (
    guess_brand,
    normalize_text,
    parse_price_to_kopecks,
)
from market_parser.stores.base import BaseStoreAdapter, StoreAdapterError, StoreMetadata
from market_parser.stores.retail_sources import (
    _camoufox_new_page,
    _id_from_url,
)

KOMUS_ORIGIN = "https://www.komus.ru"

# Komus search mixes in adult food ("смесь орехов", "каша Быстров"), so require an
# explicit baby context: a child marker or a baby-food brand.
BABY_CONTEXT_MARKERS = (
    "детск",
    "детей",
    "ребён",
    "ребен",
    "малыш",
    "младен",
    "грудн",
    "с рождения",
    "месяц",
    " мес",
    "агуша",
    "фрутоняня",
    "фрутонян",
    "nan",
    "nestogen",
    "nutrilon",
    "nutrilak",
    "gerber",
    "semper",
    "kabrita",
    "hipp",
    "нэнни",
    "similac",
    "friso",
    "бибиколь",
    "малютка",
    "беллакт",
    "humana",
)

# ...and an actual food word, or Komus returns medical/household "детский" items.
FOOD_MARKERS = (
    "пюре",
    "каша",
    "кашка",
    "смесь",
    "сок",
    "питани",
    "молоко",
    "молочк",
    "творож",
    "творог",
    "йогурт",
    "кефир",
    "биолакт",
    "нектар",
    "компот",
    "кисель",
    "вода",
    "водичка",
    "печенье",
    "хлебц",
    "батончик",
    "снек",
    "пастил",
    "кекс",
    "напиток",
    "пюрешк",
)

# Komus is a general retailer, so its "детское питание" search is noisy. Drop the
# obvious non-food hits (gift cards, soap, spices, hygiene, pet food, ...).
NON_FOOD_MARKERS = (
    "карт",
    "подарочн",
    "мыло",
    "крем",
    "шампун",
    "гель",
    "салфет",
    "пелен",
    "подгуз",
    "бумаг",
    "ручк",
    "перц",
    "приправ",
    "специ",
    "горчиц",
    "кетчуп",
    "корм",
    "порош",
    "стиральн",
    "влажн",
    "зубн",
    "крышк",
    "бутыл",
    "посуд",
    "контейнер",
    "термос",
)


class KomusAdapter(BaseStoreAdapter):
    """Komus is a Spartacus/Hybris SPA: the search page fires a stateful
    ``/api/listing`` XHR that returns the product JSON (the endpoint can't be
    hit directly). We drive the search page in Camoufox and capture that
    response per page."""

    metadata = StoreMetadata(
        slug="komus",
        name="Комус",
        channel="Е-ком",
        category_url="https://www.komus.ru/search/?text=детское питание",
    )
    search_terms = (
        "детское питание",
        "детская смесь",
        "детское пюре",
        "детская каша",
        "агуша",
        "фрутоняня",
    )
    max_pages = 5

    async def fetch_category(self, limit: int | None = None) -> list[ProductPrice]:
        effective_limit = self.effective_limit(limit)
        products: list[ProductPrice] = []
        seen: set[str] = set()
        browser = None
        try:
            browser, page = await _camoufox_new_page(self.settings)
            for term in self.search_terms:
                for page_num in range(self.max_pages):
                    payload = await self._fetch_listing(page, term, page_num)
                    if not isinstance(payload, dict):
                        break
                    for item in payload.get("results") or []:
                        product = _product_from_item(item, self.settings.category_name)
                        if product is None or product.product_id in seen:
                            continue
                        seen.add(product.product_id)
                        products.append(product)
                        if self.limit_reached(len(products), effective_limit):
                            return products
                    pagination = payload.get("pagination") or {}
                    if page_num + 1 >= int(pagination.get("numberOfPages") or 1):
                        break
                if self.limit_reached(len(products), effective_limit):
                    break
            if not products:
                raise StoreAdapterError(f"{self.metadata.name}: no products parsed")
            return products
        finally:
            if browser is not None:
                await browser.close()

    async def _fetch_listing(self, page, term: str, page_num: int) -> Any:
        url = f"{KOMUS_ORIGIN}/search/?text={quote(term)}&page={page_num}"
        try:
            async with page.expect_response(
                lambda r: "/api/listing" in r.url and "/stock" not in r.url,
                timeout=30_000,
            ) as response_info:
                await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            response = await response_info.value
            return json.loads(await response.text())
        except Exception:
            return None


def _product_from_item(item: Any, category: str) -> ProductPrice | None:
    if not isinstance(item, dict):
        return None
    name = normalize_text(str(item.get("name") or ""))
    if not name:
        return None
    folded = name.casefold()
    if not any(marker in folded for marker in BABY_CONTEXT_MARKERS):
        return None
    if not any(marker in folded for marker in FOOD_MARKERS):
        return None
    if any(marker in folded for marker in NON_FOOD_MARKERS):
        return None
    regular = parse_price_to_kopecks((item.get("price") or {}).get("value"))
    if regular is None:
        return None
    code = str(item.get("code") or "")
    url = str(item.get("url") or "")
    rating = item.get("averageRating")
    rating = float(rating) if isinstance(rating, (int, float)) and 0 < rating <= 5 else None
    stock = (item.get("stock") or {}).get("stockLevelStatus") or {}
    return ProductPrice(
        store_slug="komus",
        store_name="Комус",
        category=category,
        brand=guess_brand(name),
        product_name=name,
        product_url=urljoin(KOMUS_ORIGIN, url),
        product_id=code or _id_from_url(url or name),
        regular_price_kopecks=regular,
        rating=rating,
        availability="in_stock" if stock.get("code") == "inStock" else "unknown",
        raw={"source": "api/listing"},
    )
