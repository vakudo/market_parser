from __future__ import annotations

import asyncio

from market_parser.config import Settings
from market_parser.stores.base import StoreAdapterError


async def fetch_rendered_html(url: str, settings: Settings, *, wait_ms: int = 2500) -> str:
    try:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise StoreAdapterError("Playwright is not installed") from exc

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(
            locale="ru-RU",
            timezone_id=settings.timezone,
            user_agent=settings.user_agent,
            viewport={"width": 1440, "height": 1100},
            geolocation={"latitude": 55.7558, "longitude": 37.6173},
            permissions=["geolocation"],
        )
        page = await context.new_page()
        try:
            await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=int(settings.request_timeout_seconds * 1000),
            )
            try:
                await page.wait_for_load_state("networkidle", timeout=8000)
            except PlaywrightTimeoutError:
                pass
            await asyncio.sleep(wait_ms / 1000)
            return await page.content()
        finally:
            await context.close()
            await browser.close()
