"""Adapters for the three ServicePipe/Variti-protected stores.

samokat, perekrestok and onlinetrade block our own browser at the antibot layer
(JS proof-of-work), so they used to be manual-only via ``run_logs/cdp_*.py``.
Here we fetch their rendered HTML through a scraping API that solves the antibot
(see ``scrape_api.py``) and parse it with BeautifulSoup. The extraction mirrors
the DOM logic of the CDP collectors.

These three are auto-runnable only when a scrape API key is configured; without
one ``auto_store_slugs`` keeps them out of the daily run and they stay manual.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from market_parser.models import ProductPrice
from market_parser.normalize import guess_brand, normalize_text, parse_price_to_kopecks
from market_parser.stores.base import StoreAdapterError, StoreMetadata
from market_parser.stores.retail_sources import RetailSourceAdapter, _dedupe
from market_parser.stores.scrape_api import fetch_via_scrape_api

_RUBLE_INT = re.compile(r"^(\d[\d\s  ]*)\s*₽$")
_RUBLE_DEC = re.compile(r"^(\d[\d\s  ]*(?:[.,]\d+)?)\s*₽$")
_RUBLE_UNIT = re.compile(r"^(\d[\d\s  ]*(?:[.,]\d+)?)\s*₽\s*/\s*шт")


def _clean_num(text: str) -> str:
    return text.replace(" ", "").replace(" ", "").replace(" ", "")


def _leaf_texts(node):
    """Yield (text, leaf_element) for text-bearing leaf nodes under ``node``."""
    for string in node.find_all(string=True):
        text = string.replace(" ", " ").replace(" ", " ").strip()
        if text:
            yield text, string.parent


def _has_ancestor_class(element, pattern: re.Pattern) -> bool:
    cur = element
    while cur is not None and getattr(cur, "get", None) is not None:
        classes = cur.get("class") or []
        if any(pattern.search(c) for c in classes):
            return True
        cur = cur.parent
    return False


_OVERLAY_RE = re.compile(r"Overlay|discount", re.IGNORECASE)


class _ScrapeApiAdapter(RetailSourceAdapter):
    """RetailSourceAdapter whose fetch goes through the scraping API."""

    auto_runnable = True
    requires_scrape_api = True

    async def _scrape(self, url: str, *, scroll_steps: int) -> str:
        return await fetch_via_scrape_api(
            url, self.settings, scroll_steps=scroll_steps, wait_ms=4000
        )


class SamokatAdapter(_ScrapeApiAdapter):
    metadata = StoreMetadata(
        slug="samokat",
        name="Самокат",
        channel="Е-ком",
        category_url="https://samokat.ru/",
    )
    source_urls = ("https://samokat.ru/category/detskoe-pitanie",)
    origin = "https://samokat.ru"
    max_categories = 12

    async def fetch_category(self, limit: int | None = None) -> list[ProductPrice]:
        effective_limit = self.effective_limit(limit)
        products: list[ProductPrice] = []

        category_urls = list(self.source_urls)
        # Discover baby-food subcategory chips from the home/landing page.
        try:
            home_html = await self._scrape(self.origin, scroll_steps=0)
            for href in _samokat_category_hrefs(home_html):
                full = urljoin(self.origin, href)
                if full not in category_urls:
                    category_urls.append(full)
        except StoreAdapterError:
            pass

        for url in category_urls[: self.max_categories]:
            try:
                html = await self._scrape(url, scroll_steps=10)
            except StoreAdapterError:
                continue
            products.extend(
                parse_samokat(
                    html,
                    base_url=self.origin,
                    category=self.settings.category_name,
                )
            )
            products = _dedupe(products)
            if self.limit_reached(len(products), effective_limit):
                break
            await self.polite_page_delay()

        return products[:effective_limit] if effective_limit is not None else products


class PerekrestokAdapter(_ScrapeApiAdapter):
    metadata = StoreMetadata(
        slug="perekrestok",
        name="Перекрёсток",
        channel="Федеральная сеть",
        category_url="https://www.perekrestok.ru/cat/230/detskoe-pitanie",
    )
    source_urls = ("https://www.perekrestok.ru/cat/230/detskoe-pitanie",)
    origin = "https://www.perekrestok.ru"

    async def fetch_category(self, limit: int | None = None) -> list[ProductPrice]:
        effective_limit = self.effective_limit(limit)
        products: list[ProductPrice] = []
        for url in self.source_urls:
            try:
                html = await self._scrape(url, scroll_steps=12)
            except StoreAdapterError:
                continue
            products.extend(
                parse_perekrestok(
                    html,
                    base_url=self.origin,
                    category=self.settings.category_name,
                )
            )
            products = _dedupe(products)
            if self.limit_reached(len(products), effective_limit):
                break
            await self.polite_page_delay()
        return products[:effective_limit] if effective_limit is not None else products


class OnlinetradeAdapter(_ScrapeApiAdapter):
    metadata = StoreMetadata(
        slug="onlinetrade",
        name="Онлайнтрейд.ру",
        channel="Е-ком",
        category_url="https://www.onlinetrade.ru/catalogue/detskoe_pyure-c1210/",
    )
    origin = "https://www.onlinetrade.ru"
    subcats = (
        "detskoe_pyure-c1210", "molochnye_smesi-c1209", "kashi_molochnye-c1232",
        "kashi_bezmolochnye-c1233", "detskoe_pechene_marmelad_i_deserty-c1516",
        "detskie_napitki-c1243", "chay_detskiy-c1517", "pitanie_dlya_maloezhek-c2469",
    )
    max_pages_per_cat = 30

    async def fetch_category(self, limit: int | None = None) -> list[ProductPrice]:
        effective_limit = self.effective_limit(limit)
        products: list[ProductPrice] = []
        seen: set[str] = set()
        for sub in self.subcats:
            for page_num in range(self.max_pages_per_cat):
                url = f"{self.origin}/catalogue/{sub}/?per_page=45&page={page_num}"
                try:
                    # Server-rendered cards: no scroll needed once antibot passes.
                    html = await self._scrape(url, scroll_steps=0)
                except StoreAdapterError:
                    break
                added = 0
                for product in parse_onlinetrade(
                    html, base_url=self.origin, category=self.settings.category_name
                ):
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
                await self.polite_page_delay()
        return products


# --- parsers (ported from run_logs/cdp_*.py) ---------------------------------


def _samokat_category_hrefs(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    hrefs: list[str] = []
    for a in soup.select("a[href^='/category/']"):
        href = a.get("href")
        if href and href not in hrefs:
            hrefs.append(href)
    return hrefs


def parse_samokat(html: str, *, base_url: str, category: str) -> list[ProductPrice]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[ProductPrice] = []
    for a in soup.select("a[href*='/product/']"):
        href = a.get("href")
        if not href:
            continue
        name_el = a.select_one('[class*="ProductCard_name"]')
        name = ""
        if name_el is not None:
            name = name_el.get("title") or name_el.get_text(strip=True)
        if not name:
            img = a.find("img")
            if img is not None:
                name = img.get("alt") or ""
        if not name:
            continue
        prices: list[int] = []
        for text, leaf in _leaf_texts(a):
            if _has_ancestor_class(leaf, _OVERLAY_RE):
                continue
            m = _RUBLE_INT.match(text)
            if m:
                prices.append(int(_clean_num(m.group(1))))
        prices = sorted(set(prices))
        if not prices:
            continue
        regular = prices[-1] * 100
        promo = prices[0] * 100 if len(prices) > 1 else None
        name = normalize_text(name)
        out.append(ProductPrice(
            store_slug="samokat", store_name="Самокат", category=category,
            brand=guess_brand(name), product_name=name,
            product_url=urljoin(base_url, href),
            product_id=href.rstrip("/").split("/")[-1],
            regular_price_kopecks=regular, promo_price_kopecks=promo,
            availability="in_stock", raw={"source": "scrape_api"},
        ))
    return _dedupe(out)


def parse_perekrestok(html: str, *, base_url: str, category: str) -> list[ProductPrice]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[ProductPrice] = []
    for card in soup.select(".product-card"):
        title_a = card.select_one("a.product-card__title") or card.select_one("a[href*='/p/']")
        href = title_a.get("href") if title_a is not None else None
        if not href or "/p/" not in href:
            continue
        name = ""
        title_link = card.select_one("a.product-card__title")
        if title_link is not None:
            name = title_link.get_text(strip=True)
        if not name:
            img = card.find("img")
            if img is not None:
                name = img.get("title") or img.get("alt") or ""
        if not name:
            continue
        rating = None
        r_el = card.select_one(".rating-value")
        if r_el is not None:
            try:
                rating = float(r_el.get_text(strip=True).replace(",", "."))
            except ValueError:
                rating = None
        main: list[float] = []
        unit: list[float] = []
        for text, _leaf in _leaf_texts(card):
            m = _RUBLE_DEC.match(text)
            if m:
                main.append(float(_clean_num(m.group(1)).replace(",", ".")))
                continue
            m = _RUBLE_UNIT.match(text)
            if m:
                unit.append(float(_clean_num(m.group(1)).replace(",", ".")))
        values = sorted(set(main or unit))
        if not values:
            continue
        regular = round(values[-1] * 100)
        promo = round(values[0] * 100) if len(values) > 1 else None
        pid = re.findall(r"(\d{4,})", href)
        name = normalize_text(name)
        out.append(ProductPrice(
            store_slug="perekrestok", store_name="Перекрёсток", category=category,
            brand=guess_brand(name), product_name=name,
            product_url=urljoin(base_url, href),
            product_id=pid[-1] if pid else href.rstrip("/").split("/")[-1],
            regular_price_kopecks=regular, promo_price_kopecks=promo,
            rating=rating if (rating and 0 < rating <= 5) else None,
            availability="in_stock", raw={"source": "scrape_api"},
        ))
    return _dedupe(out)


def parse_onlinetrade(html: str, *, base_url: str, category: str) -> list[ProductPrice]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[ProductPrice] = []
    for card in soup.select(".indexGoods__item"):
        link = card.select_one("a.indexGoods__item__image") or card.select_one("a[href$='.html']")
        href = link.get("href") if link is not None else None
        if not href:
            continue
        name = ""
        n_el = card.select_one("[itemprop='name']")
        if n_el is not None:
            name = (n_el.get("content") or n_el.get_text(strip=True) or "").strip()
        if not name:
            img = card.find("img")
            if img is not None:
                name = img.get("alt") or ""
        if not name:
            continue
        price_raw = None
        p_el = card.select_one("[itemprop='price']")
        if p_el is not None:
            price_raw = p_el.get("content") or p_el.get_text(strip=True)
        if not price_raw:
            pc = card.select_one("[class*='price__']") or card.select_one("[class*='price']")
            if pc is not None:
                price_raw = pc.get_text(strip=True)
        price = parse_price_to_kopecks(price_raw)
        if not price:
            continue
        rating = None
        r_el = card.select_one("[itemprop='ratingValue']")
        if r_el is not None:
            try:
                rv = float(str(r_el.get("content") or r_el.get_text(strip=True)).replace(",", "."))
                rating = rv if 0 < rv <= 5 else None
            except (TypeError, ValueError):
                rating = None
        id_el = card.select_one("[data-itemid]")
        pid = id_el.get("data-itemid") if id_el is not None else None
        if not pid:
            m = re.findall(r"-(\d+)\.html", href)
            pid = m[-1] if m else href
        name = normalize_text(name)
        out.append(ProductPrice(
            store_slug="onlinetrade", store_name="Онлайнтрейд.ру", category=category,
            brand=guess_brand(name), product_name=name,
            product_url=urljoin(base_url, href),
            product_id=str(pid), regular_price_kopecks=price,
            rating=rating, availability="in_stock", raw={"source": "scrape_api"},
        ))
    return _dedupe(out)
