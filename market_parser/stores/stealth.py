"""Сбор Variti-магазинов (Самокат / Перекрёсток / Онлайнтрейд) без ручного участия.

Variti детектирует автоматизированные браузеры (Camoufox/Playwright), но пропускает
настоящий Chrome. Здесь используется patchright — playwright с вычищенными маркерами
автоматизации — поверх системного Chrome, headful (на сервере — под Xvfb), с постоянным
профилем на диске, чтобы куки Variti переживали перезапуски. Логика извлечения портирована
из ручных run_logs/cdp_*.py.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from contextlib import asynccontextmanager
from urllib.parse import urljoin

from market_parser.models import ProductPrice
from market_parser.normalize import guess_brand, normalize_text, parse_price_to_kopecks
from market_parser.stores.base import (
    BaseStoreAdapter,
    StoreAdapterError,
    StoreBlockedError,
    StoreMetadata,
)

# Потолок времени на один Variti-магазин: и старый драйвер patchright, и сам Variti
# могут зависнуть, а дневной прогон не должен этого ждать. При превышении — магазин ❌.
STEALTH_STORE_TIMEOUT = 300

CHALLENGE_MARKERS = re.compile(
    r"variti|разверните картинку|проверяем, что вы не робот|checking your browser",
    re.IGNORECASE,
)
# Дать Variti время на фоновую JS-проверку: при чистом профиле первый заход
# может крутить челлендж несколько секунд и пройти сам, без капчи.
CHALLENGE_WAIT_MS = 25_000
CHALLENGE_POLL_MS = 2_500


@asynccontextmanager
async def stealth_page(settings, slug: str):
    """Настоящий Chrome через patchright: персистентный профиль, прокси, Xvfb на Linux."""
    try:
        from patchright.async_api import async_playwright
    except ImportError as exc:
        raise StoreAdapterError("patchright is required for this store") from exc

    display = None
    if sys.platform != "win32" and not os.environ.get("DISPLAY"):
        try:
            from pyvirtualdisplay import Display
        except ImportError as exc:
            raise StoreAdapterError("pyvirtualdisplay is required on headless hosts") from exc
        display = Display(visible=False, size=(1440, 900))
        display.start()
    try:
        async with async_playwright() as p:
            # Абсолютный путь обязателен: Chrome резолвит относительный user-data-dir
            # от своего рабочего каталога, а не репозитория, и виснет на инициализации.
            profile_dir = (settings.browser_storage_state_dir / f"stealth_{slug}").resolve()
            profile_dir.mkdir(parents=True, exist_ok=True)
            kwargs: dict = {
                "user_data_dir": str(profile_dir),
                "channel": "chrome",
                "headless": False,
                "no_viewport": True,
                "locale": "ru-RU",
                "timezone_id": settings.timezone,
            }
            if settings.browser_proxy_server:
                proxy: dict[str, str] = {"server": settings.browser_proxy_server}
                if settings.browser_proxy_username:
                    proxy["username"] = settings.browser_proxy_username
                if settings.browser_proxy_password:
                    proxy["password"] = settings.browser_proxy_password
                kwargs["proxy"] = proxy
            # Только настоящий Chrome (channel=chrome): bundled chromium patchright'а
            # Variti не проходит и на Windows выходит мимо split-tunnel, поэтому
            # фолбэка на него нет — лучше явная ошибка магазина. Жёсткий таймаут на
            # запуск: старый драйвер patchright изредка виснет на CDP-pipe, и без
            # этого один магазин заблокировал бы воркер на store_timeout (40 мин).
            try:
                context = await asyncio.wait_for(
                    p.chromium.launch_persistent_context(**kwargs), timeout=60
                )
            except Exception as exc:  # noqa: BLE001 - любой сбой запуска = магазин недоступен
                raise StoreBlockedError(
                    f"{slug}: stealth Chrome launch failed ({type(exc).__name__})"
                ) from exc
            try:
                page = context.pages[0] if context.pages else await context.new_page()
                yield page
            finally:
                await context.close()
    finally:
        if display is not None:
            display.stop()


async def goto_through_challenge(page, url: str, *, store_name: str, ready_selector: str):
    """Открыть страницу и дождаться контента; подождать авто-прохода Variti-челленджа."""
    await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    waited = 0
    while waited <= CHALLENGE_WAIT_MS:
        try:
            if await page.locator(ready_selector).count() > 0:
                return
        except Exception:  # noqa: BLE001 - страница может перегружаться челленджем.
            pass
        body = ""
        try:
            body = await page.locator("body").inner_text(timeout=5_000)
        except Exception:  # noqa: BLE001
            pass
        if waited >= CHALLENGE_WAIT_MS and CHALLENGE_MARKERS.search(body or ""):
            raise StoreBlockedError(f"{store_name}: Variti challenge not passed")
        await page.wait_for_timeout(CHALLENGE_POLL_MS)
        waited += CHALLENGE_POLL_MS


SAMOKAT_EXTRACT_JS = r"""
() => {
  const out = [];
  for (const a of document.querySelectorAll("a[href*='/product/']")) {
    const href = a.getAttribute('href');
    if (!href) continue;
    const nameEl = a.querySelector('[class*="ProductCard_name"]');
    const img = a.querySelector('img');
    const name = (nameEl && (nameEl.getAttribute('title') || nameEl.textContent.trim()))
                 || (img && img.getAttribute('alt')) || '';
    const prices = [];
    for (const e of a.querySelectorAll('span,div')) {
      if (e.children.length) continue;
      if (e.closest('[class*="Overlay"]') || e.closest('[class*="discount"]')) continue;
      const t = (e.textContent || '').replace(/ /g, ' ').trim();
      const m = t.match(/^(\d[\d\s]*)\s*₽$/);
      if (m) prices.push(parseInt(m[1].replace(/\s/g, ''), 10));
    }
    if (name) out.push({href, name, prices});
  }
  return out;
}
"""

PEREKRESTOK_EXTRACT_JS = r"""
() => {
  const out = [];
  for (const card of document.querySelectorAll('.product-card')) {
    const titleA = card.querySelector('a.product-card__title') ||
                   card.querySelector("a[href*='/p/']");
    const href = titleA ? titleA.getAttribute('href') : null;
    if (!href || href.indexOf('/p/') < 0) continue;
    const img = card.querySelector('img');
    const name = (card.querySelector('a.product-card__title') &&
                  card.querySelector('a.product-card__title').textContent.trim())
                 || (img && (img.getAttribute('title') || img.getAttribute('alt'))) || '';
    const rEl = card.querySelector('.rating-value');
    const rating = rEl ? parseFloat(rEl.textContent.replace(',', '.')) : null;
    const main = [], unit = [];
    for (const e of card.querySelectorAll('*')) {
      if (e.children.length) continue;
      const t = (e.textContent || '').replace(/ /g, ' ').trim();
      let m = t.match(/^(\d[\d\s]*(?:[.,]\d+)?)\s*₽$/);
      if (m) { main.push(parseFloat(m[1].replace(/\s/g,'').replace(',','.'))); continue; }
      m = t.match(/^(\d[\d\s]*(?:[.,]\d+)?)\s*₽\s*\/\s*шт/);
      if (m) unit.push(parseFloat(m[1].replace(/\s/g,'').replace(',','.')));
    }
    const prices = main.length ? main : unit;
    if (name && prices.length) out.push({href, name, rating, prices});
  }
  return out;
}
"""

ONLINETRADE_EXTRACT_JS = r"""
() => {
  const out = [];
  for (const card of document.querySelectorAll('.indexGoods__item')) {
    const link = card.querySelector("a.indexGoods__item__image") ||
                 card.querySelector("a[href$='.html']");
    const href = link ? link.getAttribute('href') : null;
    const img = card.querySelector('img');
    let name = '';
    const nEl = card.querySelector("[itemprop='name']");
    if (nEl) name = (nEl.getAttribute('content') || nEl.textContent || '').trim();
    if (!name && img) name = img.getAttribute('alt') || '';
    let price = null;
    const pEl = card.querySelector("[itemprop='price']");
    if (pEl) price = pEl.getAttribute('content') || pEl.textContent;
    if (!price) {
      const pc = card.querySelector("[class*='price__']") || card.querySelector("[class*='price']");
      if (pc) price = pc.textContent;
    }
    let rating = null;
    const rEl = card.querySelector("[itemprop='ratingValue']");
    if (rEl) rating = rEl.getAttribute('content') || rEl.textContent;
    const idEl = card.querySelector('[data-itemid]');
    const id = idEl ? idEl.getAttribute('data-itemid') : null;
    if (name && href) out.push({href, name, price, rating, id});
  }
  return out;
}
"""


async def _scroll_collect(page, extract_js: str, *, max_rounds: int, stagnant_stop: int) -> dict:
    """Скроллить виртуализированную ленту, накапливая карточки по мере прокрутки."""
    products: dict[str, dict] = {}
    stagnant = 0
    for _ in range(max_rounds):
        rows = await page.evaluate(extract_js)
        before = len(products)
        for row in rows:
            if row["href"] not in products:
                products[row["href"]] = row
        stagnant = stagnant + 1 if len(products) == before else 0
        if stagnant >= stagnant_stop:
            break
        await page.mouse.wheel(0, 5500)
        await page.wait_for_timeout(700)
    return products


class StealthStoreAdapter(BaseStoreAdapter):
    """База для Variti-магазинов: общий жёсткий таймаут поверх сбора."""

    async def fetch_category(self, limit: int | None = None) -> list[ProductPrice]:
        try:
            return await asyncio.wait_for(self._collect(limit), timeout=STEALTH_STORE_TIMEOUT)
        except TimeoutError as exc:
            raise StoreBlockedError(
                f"{self.metadata.slug}: stealth collect timed out "
                f"after {STEALTH_STORE_TIMEOUT}s"
            ) from exc

    async def _collect(self, limit: int | None) -> list[ProductPrice]:
        raise NotImplementedError


class SamokatStealthAdapter(StealthStoreAdapter):
    metadata = StoreMetadata(
        slug="samokat",
        name="Самокат",
        channel="Е-ком",
        category_url="https://samokat.ru/category/detskoe-pitanie-1",
    )

    async def _collect(self, limit: int | None) -> list[ProductPrice]:
        origin = "https://samokat.ru"
        async with stealth_page(self.settings, self.metadata.slug) as page:
            await goto_through_challenge(
                page,
                self.metadata.category_url,
                store_name=self.metadata.name,
                ready_selector="a[href*='/product/']",
            )
            await page.wait_for_timeout(3_500)
            subcats = await page.evaluate(
                """() => [...document.querySelectorAll(
                       "a[data-chip-item='true'][href^='/category/']")]
                      .map(a => a.getAttribute('href'))"""
            )
            categories = [self.metadata.category_url.replace(origin, "")]
            categories += [c for c in dict.fromkeys(subcats) if c not in categories]

            products: dict[str, dict] = {}
            for category in categories:
                if self.limit_reached(len(products), limit):
                    break
                await page.goto(
                    origin + category, wait_until="domcontentloaded", timeout=60_000
                )
                await page.wait_for_timeout(3_500)
                products.update(
                    await _scroll_collect(
                        page, SAMOKAT_EXTRACT_JS, max_rounds=80, stagnant_stop=4
                    )
                )

        out: list[ProductPrice] = []
        for href, row in products.items():
            prices = sorted(set(row["prices"]))
            if not prices:
                continue
            name = normalize_text(row["name"])
            out.append(
                ProductPrice(
                    store_slug=self.metadata.slug,
                    store_name=self.metadata.name,
                    category=self.settings.category_name,
                    brand=guess_brand(name),
                    product_name=name,
                    product_url=urljoin(origin, href),
                    product_id=href.rstrip("/").split("/")[-1],
                    regular_price_kopecks=prices[-1] * 100,
                    promo_price_kopecks=prices[0] * 100 if len(prices) > 1 else None,
                    availability="in_stock",
                    raw={"source": "stealth_chrome"},
                )
            )
            if self.limit_reached(len(out), limit):
                break
        return out


class PerekrestokStealthAdapter(StealthStoreAdapter):
    metadata = StoreMetadata(
        slug="perekrestok",
        name="Перекрёсток",
        channel="Федеральная сеть",
        category_url="https://www.perekrestok.ru/cat/230/detskoe-pitanie",
    )

    async def _collect(self, limit: int | None) -> list[ProductPrice]:
        origin = "https://www.perekrestok.ru"
        async with stealth_page(self.settings, self.metadata.slug) as page:
            await goto_through_challenge(
                page,
                self.metadata.category_url,
                store_name=self.metadata.name,
                ready_selector=".product-card",
            )
            await page.wait_for_timeout(3_000)
            products = await _scroll_collect(
                page, PEREKRESTOK_EXTRACT_JS, max_rounds=200, stagnant_stop=6
            )

        out: list[ProductPrice] = []
        for href, row in products.items():
            prices = sorted(set(row["prices"]))
            if not prices:
                continue
            name = normalize_text(row["name"])
            rating = row.get("rating")
            product_ids = re.findall(r"(\d{4,})", href)
            out.append(
                ProductPrice(
                    store_slug=self.metadata.slug,
                    store_name=self.metadata.name,
                    category=self.settings.category_name,
                    brand=guess_brand(name),
                    product_name=name,
                    product_url=urljoin(origin, href),
                    product_id=product_ids[-1] if product_ids else href.rstrip("/").split("/")[-1],
                    regular_price_kopecks=round(prices[-1] * 100),
                    promo_price_kopecks=round(prices[0] * 100) if len(prices) > 1 else None,
                    rating=rating if (rating and 0 < rating <= 5) else None,
                    availability="in_stock",
                    raw={"source": "stealth_chrome"},
                )
            )
            if self.limit_reached(len(out), limit):
                break
        return out


class OnlinetradeStealthAdapter(StealthStoreAdapter):
    metadata = StoreMetadata(
        slug="onlinetrade",
        name="Онлайнтрейд.ру",
        channel="Е-ком",
        category_url="https://www.onlinetrade.ru/catalogue/detskoe_pyure-c1210/",
    )
    subcategories = (
        "detskoe_pyure-c1210",
        "molochnye_smesi-c1209",
        "kashi_molochnye-c1232",
        "kashi_bezmolochnye-c1233",
        "detskoe_pechene_marmelad_i_deserty-c1516",
        "detskie_napitki-c1243",
        "chay_detskiy-c1517",
        "pitanie_dlya_maloezhek-c2469",
    )

    async def _collect(self, limit: int | None) -> list[ProductPrice]:
        origin = "https://www.onlinetrade.ru"
        products: dict[str, dict] = {}
        async with stealth_page(self.settings, self.metadata.slug) as page:
            await goto_through_challenge(
                page,
                self.metadata.category_url,
                store_name=self.metadata.name,
                ready_selector=".indexGoods__item",
            )
            for subcategory in self.subcategories:
                if self.limit_reached(len(products), limit):
                    break
                for page_num in range(0, 30):
                    url = f"{origin}/catalogue/{subcategory}/?per_page=45&page={page_num}"
                    await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                    try:
                        await page.wait_for_selector(".indexGoods__item", timeout=8_000)
                    except Exception:  # noqa: BLE001 - пустая страница = конец пагинации.
                        pass
                    await page.wait_for_timeout(1_500)
                    rows = await page.evaluate(ONLINETRADE_EXTRACT_JS)
                    added = 0
                    for row in rows:
                        if row["href"] in products:
                            continue
                        products[row["href"]] = row
                        added += 1
                    if added == 0 or self.limit_reached(len(products), limit):
                        break
                    # Онлайнтрейд блокирует быструю навигацию — не частить.
                    await self.polite_page_delay()

        out: list[ProductPrice] = []
        for href, row in products.items():
            price = parse_price_to_kopecks(row["price"])
            if not price:
                continue
            name = normalize_text(row["name"])
            rating = None
            try:
                value = float(str(row["rating"]).replace(",", "."))
                rating = value if 0 < value <= 5 else None
            except (TypeError, ValueError):
                pass
            product_id = row["id"] or (re.findall(r"-(\d+)\.html", href) or [href])[-1]
            out.append(
                ProductPrice(
                    store_slug=self.metadata.slug,
                    store_name=self.metadata.name,
                    category=self.settings.category_name,
                    brand=guess_brand(name),
                    product_name=name,
                    product_url=urljoin(origin, href),
                    product_id=str(product_id),
                    regular_price_kopecks=price,
                    rating=rating,
                    availability="in_stock",
                    raw={"source": "stealth_chrome"},
                )
            )
            if self.limit_reached(len(out), limit):
                break
        return out
