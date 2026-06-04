from __future__ import annotations

import asyncio
from urllib.parse import urlencode

from market_parser.models import ProductPrice
from market_parser.normalize import KNOWN_BRANDS, guess_brand, normalize_text, wb_units_to_kopecks
from market_parser.stores.base import (
    BaseStoreAdapter,
    StoreAdapterError,
    StoreMetadata,
    StoreRateLimitedError,
)
from market_parser.stores.retail_sources import _camoufox_get_json, _camoufox_new_page

EXCLUDED_SUBJECT_IDS = {
    "2647",  # Питание для кормящих и беременных, not baby food.
    "7625",  # Соусы детские is noisy in WB and often contains adult sauces.
}

BABY_CONTEXT_MARKERS = (
    "детск",
    "детям",
    "ребен",
    "ребён",
    "малыш",
    "младен",
    "грудн",
    "месяц",
    "мес",
    "с рождения",
)

CORE_BABY_FOOD_MARKERS = (
    "смесь",
    "каша",
    "молочко",
    "пюре",
)

GENERAL_FOOD_MARKERS = (
    "сок",
    "нектар",
    "компот",
    "кисель",
    "морс",
    "смузи",
    "вода",
    "печенье",
    "батончик",
    "макарон",
    "суп",
    "чай",
    "кефир",
    "творог",
    "йогурт",
    "молоко",
    "пауч",
)

NON_BABY_FOOD_MARKERS = (
    "для кош",
    "для собак",
    "кошач",
    "собач",
    "корм для",
    "фуа-гра",
    "фуа гра",
    "острый",
    "острая",
    "рамен",
    "соус",
    "аджика",
    "майонез",
    "кетчуп",
    "горчица",
    "протеин",
    "sport",
    "спортив",
    "фитнес",
)

TRUSTED_BABY_BRANDS = {brand.casefold() for brand in KNOWN_BRANDS}


class WildberriesAdapter(BaseStoreAdapter):
    metadata = StoreMetadata(
        slug="wildberries",
        name="Вайлдберриз",
        channel="Е-ком",
        category_url="https://www.wildberries.ru/catalog/pitanie/detskoe-pitanie",
    )

    api_url = "https://catalog.wb.ru/catalog/product5/v4/catalog"
    subject_base_url = "https://static-basket-01.wb.ru/vol0/data/subject-base.json"
    subject_id = "2638"
    max_pages_per_query = 50
    max_products_per_query = 5000
    request_spacing_seconds = 2.0  # pace API calls to avoid WB's 429
    rate_limit_backoff_seconds = 15.0
    rate_limit_max_retries = 8

    async def fetch_category(self, limit: int | None = None) -> list[ProductPrice]:
        effective_limit = self.effective_limit(limit)
        products: list[ProductPrice] = []
        seen_ids: set[str] = set()
        browser = None
        try:
            browser, page = await _camoufox_new_page(self.settings)
            self._page = page
            await self._warm_up(page)
            subject_ids = await self._fetch_subject_ids()
            allowed_subject_ids = set(subject_ids)
            try:
                for subject_id in subject_ids:
                    if self.limit_reached(len(products), effective_limit):
                        break
                    first_payload = await self._fetch_page(1, subject_id=subject_id)
                    total = _payload_total(first_payload)
                    if total > self.max_products_per_query:
                        brand_ids = await self._fetch_brand_ids_for_subject(
                            subject_id, first_payload
                        )
                        for brand_id in brand_ids:
                            await self._collect_query(
                                products=products,
                                seen_ids=seen_ids,
                                limit=effective_limit,
                                subject_id=subject_id,
                                allowed_subject_ids=allowed_subject_ids,
                                brand_id=brand_id,
                            )
                            if self.limit_reached(len(products), effective_limit):
                                break
                    else:
                        await self._collect_query(
                            products=products,
                            seen_ids=seen_ids,
                            limit=effective_limit,
                            subject_id=subject_id,
                            allowed_subject_ids=allowed_subject_ids,
                            first_payload=first_payload,
                        )
            except StoreRateLimitedError:
                # Keep everything collected so far instead of discarding it.
                if not products:
                    raise
        finally:
            if browser is not None:
                await browser.close()
        return products

    async def _warm_up(self, page) -> None:
        """WB blocks the catalog API for datacenter/home IPs, but a real Camoufox
        visit to the category page sets the cookies that make the same API return
        data when called from within the browser context."""
        try:
            await page.goto(
                self.metadata.category_url,
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            await page.wait_for_timeout(5_000)
        except Exception:
            pass

    async def _get_json(self, url: str, params: dict[str, str] | None = None):
        await asyncio.sleep(self.request_spacing_seconds)
        full = url if not params else f"{url}?{urlencode(params)}"
        return await _camoufox_get_json(self._page, full, store_name=self.metadata.name)

    async def _collect_query(
        self,
        *,
        products: list[ProductPrice],
        seen_ids: set[str],
        limit: int | None,
        subject_id: str,
        allowed_subject_ids: set[str],
        brand_id: str | None = None,
        first_payload: dict | None = None,
    ) -> None:
        page = 1
        rl_retries = 0
        total = _payload_total(first_payload or {})
        while not self.limit_reached(len(products), limit):
            try:
                payload = (
                    first_payload
                    if page == 1 and first_payload is not None
                    else await self._fetch_page(page, subject_id=subject_id, brand_id=brand_id)
                )
                rl_retries = 0
            except StoreRateLimitedError:
                if rl_retries < self.rate_limit_max_retries:
                    rl_retries += 1
                    await asyncio.sleep(self.rate_limit_backoff_seconds * rl_retries)
                    continue
                raise

            total = max(total, _payload_total(payload))
            page_products = _products_from_payload(payload)
            if not page_products:
                break
            added = self._append_new_products(
                page_products,
                products,
                seen_ids,
                limit,
                allowed_subject_ids,
            )
            if not added:
                break
            if len(page_products) < 100:
                break
            page += 1
            if page > self.max_pages_per_query:
                if total > self.max_products_per_query:
                    raise StoreAdapterError(
                        f"Wildberries query exceeds the {self.max_products_per_query} "
                        f"product page cap for subject {subject_id}"
                        f"{f', brand {brand_id}' if brand_id else ''}"
                    )
                break
            await self.polite_page_delay()

    def _append_new_products(
        self,
        page_products: list[dict],
        products: list[ProductPrice],
        seen_ids: set[str],
        limit: int | None,
        allowed_subject_ids: set[str],
    ) -> int:
        added = 0
        for item in page_products:
            if not _is_baby_food_item(item, self.subject_id, allowed_subject_ids):
                continue
            item_id = str(item.get("id") or "")
            if not item_id or item_id in seen_ids:
                continue
            seen_ids.add(item_id)
            products.append(self._product_from_api_item(item))
            added += 1
            if self.limit_reached(len(products), limit):
                break
        return added

    async def _fetch_subject_ids(self) -> list[str]:
        try:
            data = await self._get_json(self.subject_base_url)
        except StoreAdapterError:
            return [self.subject_id]

        node = _find_subject_node(data, int(self.subject_id))
        child_ids = [
            str(child.get("id"))
            for child in (node or {}).get("childs", [])
            if child.get("id") is not None and str(child.get("id")) not in EXCLUDED_SUBJECT_IDS
        ]
        return child_ids or [self.subject_id]

    async def _fetch_brand_ids_for_subject(
        self,
        subject_id: str,
        first_payload: dict,
    ) -> list[str]:
        brand_ids = _brand_ids_from_payload(first_payload)
        for page in range(2, self.max_pages_per_query + 1):
            await self.polite_page_delay()
            payload = await self._fetch_page(page, subject_id=subject_id)
            page_products = _products_from_payload(payload)
            if not page_products:
                break
            brand_ids.update(_brand_ids_from_payload(payload))
            if len(page_products) < 100:
                break
        return sorted(brand_ids, key=int)

    async def _fetch_page(
        self,
        page: int,
        *,
        subject_id: str | None = None,
        brand_id: str | None = None,
    ) -> dict:
        payload = await self._get_json(
            self.api_url,
            self._catalog_params(page, subject_id=subject_id, brand_id=brand_id),
        )
        return payload if isinstance(payload, dict) else {}

    def _catalog_params(
        self,
        page: int,
        *,
        subject_id: str | None = None,
        brand_id: str | None = None,
    ) -> dict[str, str]:
        params = {
            "appType": "1",
            "curr": "rub",
            "dest": "-1257786",
            "sort": "popular",
            "spp": "30",
            "subject": subject_id or self.subject_id,
            "page": str(page),
        }
        if brand_id is not None:
            params["fbrand"] = brand_id
        return params

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

        name = str(item.get("name") or "").strip()
        brand = _brand_from_item(item, name)
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
            rating=_rating_from_item(item),
            availability="in_stock" if item.get("totalQuantity", 0) else "unknown",
            raw={
                "supplier": item.get("supplier"),
                "entity": item.get("entity"),
                "subjectId": item.get("subjectId"),
                "subjectParentId": item.get("subjectParentId"),
                "brandId": item.get("brandId"),
            },
        )


def _rating_from_item(item: dict) -> float | None:
    """Wildberries exposes the aggregate review score under a few possible keys
    depending on the catalog API version."""
    for key in ("reviewRating", "nmReviewRating", "rating"):
        value = item.get(key)
        if isinstance(value, (int, float)) and 0 < float(value) <= 5:
            return round(float(value), 1)
    return None


def _products_from_payload(payload: dict) -> list[dict]:
    products = payload.get("products")
    if isinstance(products, list):
        return products
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("products"), list):
        return data["products"]
    return []


def _payload_total(payload: dict) -> int:
    try:
        return int(payload.get("total") or 0)
    except (TypeError, ValueError):
        return 0


def _brand_ids_from_payload(payload: dict) -> set[str]:
    return {
        str(product.get("brandId"))
        for product in _products_from_payload(payload)
        if product.get("brandId") is not None
    }


def _find_subject_node(items, subject_id: int) -> dict | None:
    if not isinstance(items, list):
        return None
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("id") == subject_id:
            return item
        found = _find_subject_node(item.get("childs") or [], subject_id)
        if found is not None:
            return found
    return None


def _is_baby_food_item(
    item: dict,
    parent_subject_id: str,
    allowed_subject_ids: set[str],
) -> bool:
    subject_id = str(item.get("subjectId") or "")
    subject_parent_id = str(item.get("subjectParentId") or "")
    if subject_id in EXCLUDED_SUBJECT_IDS:
        return False
    if subject_parent_id != parent_subject_id and subject_id not in allowed_subject_ids:
        return False

    # The item is already inside an allowed baby-food subject, so WB has itself
    # categorised it as baby food — keep it, only dropping obvious cross-listed
    # non-food (pet food, sauces, sport nutrition, ...).
    text = normalize_text(
        " ".join(
            str(value or "")
            for value in (
                item.get("brand"),
                item.get("name"),
                item.get("entity"),
            )
        )
    ).casefold()
    if any(marker in text for marker in NON_BABY_FOOD_MARKERS):
        return False
    return True


def _brand_from_item(item: dict, name: str) -> str:
    brand = normalize_text(str(item.get("brand") or ""))
    if brand:
        return brand
    guessed = guess_brand(name)
    if guessed:
        return guessed
    quoted = _quoted_leading_brand(name)
    if quoted:
        return quoted
    return "Не указан WB"


def _has_trusted_baby_brand(item: dict, text: str) -> bool:
    brand = normalize_text(str(item.get("brand") or "")).casefold()
    if brand and brand in TRUSTED_BABY_BRANDS:
        return True
    return any(known_brand in text for known_brand in TRUSTED_BABY_BRANDS)


def _quoted_leading_brand(name: str) -> str:
    normalized = normalize_text(name)
    for quote in ('"', "«"):
        if not normalized.startswith(quote):
            continue
        closing = '"' if quote == '"' else "»"
        end = normalized.find(closing, 1)
        if 1 < end <= 40:
            return normalized[1:end].strip()
    return ""
