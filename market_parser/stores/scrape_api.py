"""Fetch rendered HTML through a third-party scraping API.

The samokat / perekrestok / onlinetrade sites sit behind ServicePipe (a.k.a.
Variti), whose JS proof-of-work challenge blocks an automated browser even from
a residential IP — proven locally and on Railway. To collect them unattended we
delegate the antibot pass and the RU residential exit IP to a scraping provider.
Three are supported via ``MARKET_PARSER_SCRAPE_API_PROVIDER``: ``zyte`` (default;
pay-as-you-go, cheapest at this volume), ``zenrows`` and ``scrapfly``. The
provider returns the final rendered HTML, which the per-store parsers in
``variti.py`` then scrape.
"""
from __future__ import annotations

import json

import httpx

from market_parser.config import Settings
from market_parser.stores.base import StoreAdapterError, StoreBlockedError


def scrape_api_blocked(html: str) -> bool:
    """True if ``html`` is still the ServicePipe challenge stub, not real content."""
    if not html or len(html) < 2000:
        head = (html or "").lower()
        if "servicepipe.ru" in head or "id_spinner" in head or "id_captcha_frame_div" in head:
            return True
    return "servicepipe.ru/static" in html.lower()


def _zenrows_scroll_instructions(scroll_steps: int, pause_ms: int) -> str:
    steps: list[dict] = []
    for _ in range(max(scroll_steps, 0)):
        steps.append({"scroll_y": 6000})
        steps.append({"wait": pause_ms})
    return json.dumps(steps, separators=(",", ":"))


async def _fetch_zenrows(
    url: str,
    settings: Settings,
    *,
    scroll_steps: int,
    wait_ms: int,
) -> str:
    params = {
        "url": url,
        "apikey": settings.scrape_api_key,
        "js_render": "true",
        "antibot": "true",
        "premium_proxy": "true",
        "proxy_country": settings.scrape_api_country,
        "wait": str(wait_ms),
    }
    if scroll_steps > 0:
        params["js_instructions"] = _zenrows_scroll_instructions(scroll_steps, 1200)
    async with httpx.AsyncClient(timeout=settings.scrape_api_timeout_seconds) as client:
        response = await client.get("https://api.zenrows.com/v1/", params=params)
        if response.status_code in {401, 403}:
            raise StoreAdapterError(f"ZenRows auth/credit error HTTP {response.status_code}")
        if response.status_code == 422:
            # ZenRows could not bypass the antibot for this request.
            raise StoreBlockedError("ZenRows could not bypass the antibot (422)")
        response.raise_for_status()
        return response.text


async def _fetch_zyte(
    url: str,
    settings: Settings,
    *,
    scroll_steps: int,
    wait_ms: int,
) -> str:
    body: dict = {
        "url": url,
        "browserHtml": True,
        "geolocation": (settings.scrape_api_country or "ru").upper(),
    }
    actions: list[dict] = [{"action": "waitForTimeout", "timeout": max(wait_ms, 0) / 1000}]
    if scroll_steps > 0:
        # scrollBottom auto-scrolls to load lazy/virtualized content; bounded by
        # the step count and Zyte's 60s total action budget.
        actions.append(
            {"action": "scrollBottom", "maxScrollCount": scroll_steps, "maxScrollDelay": 1}
        )
    body["actions"] = actions
    async with httpx.AsyncClient(timeout=settings.scrape_api_timeout_seconds) as client:
        response = await client.post(
            "https://api.zyte.com/v1/extract",
            json=body,
            auth=(settings.scrape_api_key or "", ""),
        )
        if response.status_code in {401, 403}:
            raise StoreAdapterError(f"Zyte auth error HTTP {response.status_code}")
        if response.status_code == 429:
            raise StoreBlockedError("Zyte rate limited (429)")
        if response.status_code >= 500:
            raise StoreBlockedError(f"Zyte could not fetch the page (HTTP {response.status_code})")
        response.raise_for_status()
        html = response.json().get("browserHtml")
        if not html:
            raise StoreBlockedError("Zyte returned no browserHtml")
        return html


async def _fetch_scrapfly(
    url: str,
    settings: Settings,
    *,
    scroll_steps: int,
    wait_ms: int,
) -> str:
    params = {
        "url": url,
        "key": settings.scrape_api_key,
        "render_js": "true",
        "asp": "true",
        "country": settings.scrape_api_country,
        "rendering_wait": str(wait_ms),
    }
    if scroll_steps > 0:
        scenario = [{"scroll": {"selector": "body", "y": 6000}}, {"wait": 1200}] * scroll_steps
        params["js_scenario"] = json.dumps(scenario, separators=(",", ":"))
    async with httpx.AsyncClient(timeout=settings.scrape_api_timeout_seconds) as client:
        response = await client.get("https://api.scrapfly.io/scrape", params=params)
        if response.status_code in {401, 403}:
            raise StoreAdapterError(f"Scrapfly auth/credit error HTTP {response.status_code}")
        response.raise_for_status()
        payload = response.json()
        result = payload.get("result", {})
        content = result.get("content")
        if not content:
            raise StoreBlockedError("Scrapfly returned no content")
        return content


async def fetch_via_scrape_api(
    url: str,
    settings: Settings,
    *,
    scroll_steps: int = 0,
    wait_ms: int = 4000,
) -> str:
    """Return final rendered HTML for ``url`` via the configured scraping API.

    Raises ``StoreAdapterError`` if no API key is set or the provider is unknown,
    and ``StoreBlockedError`` if the provider could not get past the antibot.
    """
    if not settings.scrape_api_configured:
        raise StoreAdapterError("scrape API key is not configured")
    provider = (settings.scrape_api_provider or "zenrows").lower()
    if provider == "zyte":
        html = await _fetch_zyte(url, settings, scroll_steps=scroll_steps, wait_ms=wait_ms)
    elif provider == "zenrows":
        html = await _fetch_zenrows(url, settings, scroll_steps=scroll_steps, wait_ms=wait_ms)
    elif provider == "scrapfly":
        html = await _fetch_scrapfly(url, settings, scroll_steps=scroll_steps, wait_ms=wait_ms)
    else:
        raise StoreAdapterError(f"unknown scrape API provider '{provider}'")
    if scrape_api_blocked(html):
        raise StoreBlockedError(f"scrape API still returned the antibot challenge for {url}")
    return html
