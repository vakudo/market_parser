from __future__ import annotations

import json
import re
from collections.abc import Iterable
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from market_parser.models import ProductPrice, utc_now
from market_parser.normalize import (
    choose_price_fields,
    extract_price_values,
    guess_brand,
    normalize_text,
)
from market_parser.stores.base import StoreBlockedError

ANTI_BOT_MARKERS = [
    "ваш браузер не смог пройти",
    "для доступа к веб-ресурсу включите",
    "ваш ip адрес",
    "id запроса к ресурсу",
    "access denied",
    "доступ ограничен",
    "вы не робот",
    "проблемы со связью",
    "servicepipe",
    "qrator",
    "smartcaptcha",
    "antibot challenge page",
    "captcha-api.yandex",
]


def ensure_not_blocked(html: str, store_name: str) -> None:
    if _looks_like_catalog(html):
        return
    lowered = normalize_text(html).lower()
    if any(marker in lowered for marker in ANTI_BOT_MARKERS):
        raise StoreBlockedError(f"{store_name}: anti-bot or captcha page returned")
    if "captcha" in lowered:
        raise StoreBlockedError(f"{store_name}: anti-bot or captcha page returned")


def _looks_like_catalog(html: str) -> bool:
    return any(
        marker in html
        for marker in (
            "data-product-id",
            "application/ld+json",
            "data-testid=\"product",
            "/product/",
            "/good/",
            "schema.org/Product",
        )
    )


def parse_detmir_cards(
    html: str,
    *,
    category: str,
    limit: int | None,
) -> list[ProductPrice]:
    ensure_not_blocked(html, "Детский мир")
    soup = BeautifulSoup(html, "html.parser")
    items: list[ProductPrice] = []

    for section in soup.select("section[data-product-id]"):
        product_id = section.get("data-product-id") or ""
        title_link = section.select_one('[data-testid="titleLink"]')
        title = normalize_text(title_link.get_text(" ", strip=True) if title_link else "")
        if not title:
            image = section.select_one("img[alt]")
            title = normalize_text(image.get("alt") if image else "")
        if not title:
            continue

        href = title_link.get("href") if title_link else ""
        url = urljoin("https://www.detmir.ru", href)
        price_box = section.select_one('[data-testid="productPrice"]') or section
        current = price_box.select_one(".diTAa")
        old = price_box.select_one(".iwVsa")
        current_price = extract_price_values(current.get_text(" ", strip=True) if current else "")
        old_price = extract_price_values(old.get_text(" ", strip=True) if old else "")

        regular = old_price[0] if old_price else (current_price[0] if current_price else None)
        promo = current_price[0] if old_price and current_price else None
        items.append(
            ProductPrice(
                store_slug="detmir",
                store_name="Детский мир",
                category=category,
                brand=guess_brand(title),
                product_name=title,
                product_url=url,
                product_id=product_id,
                regular_price_kopecks=regular,
                promo_price_kopecks=promo,
                availability="in_stock",
            )
        )
        if limit is not None and len(items) >= limit:
            break
    return items


def parse_vprok_cards(
    html: str,
    *,
    category: str,
    limit: int | None,
) -> list[ProductPrice]:
    ensure_not_blocked(html, "Перекресток ВПРОК")
    soup = BeautifulSoup(html, "html.parser")
    items: list[ProductPrice] = []

    for article in soup.select("article"):
        title_link = article.select_one('a[class*="longName"][href*="/product/"]')
        if not title_link:
            continue
        title = normalize_text(title_link.get("title") or title_link.get_text(" ", strip=True))
        if not title:
            continue
        href = title_link.get("href") or ""
        old_node = article.select_one('[class*="Price_role_old"]')
        current_node = article.select_one('[class*="Price_role_discount"]')
        if current_node is None:
            current_node = article.select_one(
                '[class*="Purchase_currentPrice"] [class*="Price_price"]'
            )
        current_prices = extract_price_values(
            current_node.get_text(" ", strip=True) if current_node else ""
        )
        old_prices = extract_price_values(old_node.get_text(" ", strip=True) if old_node else "")
        regular = old_prices[0] if old_prices else (current_prices[0] if current_prices else None)
        promo = current_prices[0] if old_prices and current_prices else None
        product_id = _id_from_href(href)
        items.append(
            ProductPrice(
                store_slug="vprok",
                store_name="Перекресток ВПРОК",
                category=category,
                brand=guess_brand(title),
                product_name=title,
                product_url=urljoin("https://www.vprok.ru", href),
                product_id=product_id,
                regular_price_kopecks=regular,
                promo_price_kopecks=promo,
                availability="in_stock",
            )
        )
        if limit is not None and len(items) >= limit:
            break
    return items


def parse_yandex_market_cards(
    html: str,
    *,
    category: str,
    limit: int | None,
) -> list[ProductPrice]:
    ensure_not_blocked(html, "Яндекс.Маркет")
    soup = BeautifulSoup(html, "html.parser")
    items: list[ProductPrice] = []

    for node in soup.select('[data-baobab-name="productSnippet"]'):
        candidate = None
        for payload in _extract_json_objects(node.get_text(" ", strip=True), '{"widgets"'):
            candidate = _find_yandex_offer(payload)
            if candidate:
                break
        if not candidate:
            continue
        title = normalize_text(str(candidate.get("title") or ""))
        if not title:
            continue
        href_node = node.select_one('a[href*="/card/"]')
        href = href_node.get("href") if href_node else ""
        current = _yandex_price_value(candidate.get("price"))
        old = _yandex_price_value(candidate.get("oldPrice"))
        regular = old if old else current
        promo = current if old and current else None
        items.append(
            ProductPrice(
                store_slug="yandex_market",
                store_name="Яндекс.Маркет",
                category=category,
                brand=guess_brand(title),
                product_name=title,
                product_url=urljoin("https://market.yandex.ru", href),
                product_id=str(
                    candidate.get("skuId") or candidate.get("productId") or _id_from_href(href)
                ),
                regular_price_kopecks=regular,
                promo_price_kopecks=promo,
                availability="in_stock" if candidate.get("isAvailable", True) else "unknown",
            )
        )
        if limit is not None and len(items) >= limit:
            break
    return _dedupe(items)


def parse_json_ld_products(
    html: str,
    *,
    store_slug: str,
    store_name: str,
    category: str,
    base_url: str,
) -> list[ProductPrice]:
    soup = BeautifulSoup(html, "html.parser")
    products: list[ProductPrice] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for product in _iter_json_ld_products(data):
            name = normalize_text(str(product.get("name") or ""))
            if not name:
                continue
            offers = product.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            price = offers.get("price") or offers.get("lowPrice")
            regular, promo, loyalty = choose_price_fields(extract_price_values(str(price)))
            url = product.get("url") or offers.get("url") or base_url
            products.append(
                ProductPrice(
                    store_slug=store_slug,
                    store_name=store_name,
                    category=category,
                    brand=normalize_text(str(product.get("brand") or "")) or guess_brand(name),
                    product_name=name,
                    product_url=urljoin(base_url, str(url)),
                    product_id=str(product.get("sku") or product.get("productID") or ""),
                    regular_price_kopecks=regular,
                    promo_price_kopecks=promo,
                    loyalty_price_kopecks=loyalty,
                    availability="unknown",
                )
            )
    return products


def parse_generic_product_cards(
    html: str,
    *,
    store_slug: str,
    store_name: str,
    category: str,
    base_url: str,
    product_href_patterns: Iterable[str],
    limit: int | None,
) -> list[ProductPrice]:
    ensure_not_blocked(html, store_name)
    products = parse_json_ld_products(
        html,
        store_slug=store_slug,
        store_name=store_name,
        category=category,
        base_url=base_url,
    )
    if limit is not None and len(products) >= limit:
        return _dedupe(products)[:limit]

    soup = BeautifulSoup(html, "html.parser")
    patterns = [re.compile(pattern) for pattern in product_href_patterns]
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href") or "")
        if not any(pattern.search(href) for pattern in patterns):
            continue
        title = _title_from_anchor(anchor)
        if len(title) < 8:
            continue
        container_text = _nearby_card_text(anchor)
        prices = extract_price_values(_money_context(container_text))
        if not prices:
            continue
        regular, promo, loyalty = choose_price_fields(
            prices,
            loyalty_context=("карт" in container_text.lower()),
        )
        product_id = _id_from_href(href)
        products.append(
            ProductPrice(
                store_slug=store_slug,
                store_name=store_name,
                category=category,
                brand=guess_brand(title),
                product_name=title,
                product_url=urljoin(base_url, href),
                product_id=product_id,
                regular_price_kopecks=regular,
                promo_price_kopecks=promo,
                loyalty_price_kopecks=loyalty,
                availability="unknown",
                collected_at=utc_now(),
            )
        )
        products = _dedupe(products)
        if limit is not None and len(products) >= limit:
            break
    products = _dedupe(products)
    return products[:limit] if limit is not None else products


def _extract_json_objects(text: str, marker: str):
    decoder = json.JSONDecoder()
    offset = 0
    while True:
        start = text.find(marker, offset)
        if start < 0:
            return
        try:
            value, end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            offset = start + len(marker)
            continue
        if isinstance(value, dict):
            yield value
        offset = start + max(end, len(marker))


def _find_yandex_offer(value) -> dict | None:
    if isinstance(value, dict):
        if value.get("title") and isinstance(value.get("price"), dict):
            price = value["price"]
            if price.get("value"):
                return value
        for child in value.values():
            found = _find_yandex_offer(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_yandex_offer(child)
            if found:
                return found
    return None


def _yandex_price_value(value) -> int | None:
    if not isinstance(value, dict):
        return None
    price = value.get("value")
    if price is None:
        return None
    return int(float(str(price).replace(",", ".")) * 100)


def _iter_json_ld_products(data):
    if isinstance(data, list):
        for item in data:
            yield from _iter_json_ld_products(item)
    elif isinstance(data, dict):
        item_type = data.get("@type")
        if item_type == "Product" or (isinstance(item_type, list) and "Product" in item_type):
            yield data
        for key in ("itemListElement", "mainEntity", "@graph"):
            child = data.get(key)
            if child:
                yield from _iter_json_ld_products(child)
        if "item" in data:
            yield from _iter_json_ld_products(data["item"])


def _title_from_anchor(anchor) -> str:
    text = normalize_text(anchor.get_text(" ", strip=True))
    if text:
        return text
    image = anchor.find("img", alt=True)
    return normalize_text(image.get("alt") if image else "")


def _nearby_card_text(anchor) -> str:
    current = anchor
    for _ in range(5):
        current = current.parent
        if current is None:
            break
        text = normalize_text(current.get_text(" ", strip=True))
        if "₽" in text or "руб" in text.lower():
            if len(text) < 2500:
                return text
    return normalize_text(anchor.get_text(" ", strip=True))


def _money_context(text: str) -> str:
    fragments = re.findall(
        r"\d[\d\s.,]{0,16}\s*(?:₽|руб\.?|р\.|в‚Ѕ|СЂСѓР±)",
        normalize_text(text),
        flags=re.IGNORECASE,
    )
    return " ".join(fragments) if fragments else text


def _id_from_href(href: str) -> str:
    numbers = re.findall(r"\d{5,}", href)
    return numbers[-1] if numbers else href.strip("/")


def _dedupe(products: list[ProductPrice]) -> list[ProductPrice]:
    seen: set[tuple[str, str]] = set()
    result: list[ProductPrice] = []
    for product in products:
        key = (product.store_slug, product.product_id or product.product_url)
        if key in seen:
            continue
        seen.add(key)
        result.append(product)
    return result
