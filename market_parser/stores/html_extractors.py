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
]


def ensure_not_blocked(html: str, store_name: str) -> None:
    lowered = normalize_text(html).lower()
    if any(marker in lowered for marker in ANTI_BOT_MARKERS):
        raise StoreBlockedError(f"{store_name}: anti-bot or captcha page returned")
    if "captcha" in lowered and not _looks_like_catalog(html):
        raise StoreBlockedError(f"{store_name}: anti-bot or captcha page returned")


def _looks_like_catalog(html: str) -> bool:
    return any(
        marker in html
        for marker in (
            "data-product-id",
            "application/ld+json",
            "data-testid=\"product",
            "/product/",
        )
    )


def parse_detmir_cards(
    html: str,
    *,
    category: str,
    limit: int,
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
        if len(items) >= limit:
            break
    return items


def parse_vprok_cards(
    html: str,
    *,
    category: str,
    limit: int,
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
        if len(items) >= limit:
            break
    return items


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
    limit: int,
) -> list[ProductPrice]:
    ensure_not_blocked(html, store_name)
    products = parse_json_ld_products(
        html,
        store_slug=store_slug,
        store_name=store_name,
        category=category,
        base_url=base_url,
    )
    if len(products) >= limit:
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
        prices = extract_price_values(container_text)
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
        if len(products) >= limit:
            break
    return _dedupe(products)[:limit]


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
