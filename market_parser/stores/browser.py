from __future__ import annotations

import asyncio
import re
from pathlib import Path
from urllib.parse import urlsplit

from market_parser.config import Settings
from market_parser.stores.base import StoreAdapterError

STEALTH_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['ru-RU', 'ru', 'en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
window.chrome = window.chrome || { runtime: {} };
"""


async def fetch_rendered_html(
    url: str,
    settings: Settings,
    *,
    wait_ms: int = 2500,
    wait_selector: str | None = None,
    scroll_steps: int = 0,
    scroll_pause_ms: int = 1200,
) -> str:
    try:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise StoreAdapterError("Playwright is not installed") from exc

    async with async_playwright() as playwright:
        launch_kwargs = {
            "headless": settings.browser_headless,
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        if settings.browser_proxy_server:
            proxy = {"server": settings.browser_proxy_server}
            if settings.browser_proxy_username:
                proxy["username"] = settings.browser_proxy_username
            if settings.browser_proxy_password:
                proxy["password"] = settings.browser_proxy_password
            launch_kwargs["proxy"] = proxy
        browser = await playwright.chromium.launch(**launch_kwargs)
        state_path = _storage_state_path(settings, url)
        context_kwargs = {
            "locale": "ru-RU",
            "timezone_id": settings.timezone,
            "user_agent": settings.user_agent,
            "viewport": {"width": 1440, "height": 1100},
            "geolocation": {"latitude": 55.7558, "longitude": 37.6173},
            "permissions": ["geolocation"],
        }
        if state_path.exists():
            context_kwargs["storage_state"] = str(state_path)
        context = await browser.new_context(
            **context_kwargs,
        )
        await context.add_init_script(STEALTH_SCRIPT)
        page = await context.new_page()
        try:
            try:
                await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=int(settings.request_timeout_seconds * 1000),
                )
            except PlaywrightTimeoutError:
                return await _safe_page_content(page)
            try:
                await page.wait_for_load_state("networkidle", timeout=8000)
            except PlaywrightTimeoutError:
                pass
            if wait_selector:
                try:
                    await page.wait_for_selector(wait_selector, timeout=15000)
                except PlaywrightTimeoutError:
                    pass
            for _ in range(scroll_steps):
                await page.mouse.wheel(0, 2500)
                await asyncio.sleep(scroll_pause_ms / 1000)
            await asyncio.sleep(wait_ms / 1000)
            return await _safe_page_content(page)
        finally:
            try:
                state_path.parent.mkdir(parents=True, exist_ok=True)
                await context.storage_state(path=str(state_path))
            except Exception:
                pass
            await context.close()
            await browser.close()


async def _safe_page_content(page) -> str:
    for _ in range(3):
        try:
            return await page.content()
        except Exception:
            await asyncio.sleep(1)
    return ""


def _storage_state_path(settings: Settings, url: str) -> Path:
    host = urlsplit(url).netloc or "default"
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", host).strip("_") or "default"
    return settings.browser_storage_state_dir / f"{slug}.json"
