import asyncio

import pytest

from market_parser.config import Settings
from market_parser.runner import auto_store_slugs
from market_parser.stores import scrape_api
from market_parser.stores.base import StoreAdapterError
from market_parser.stores.scrape_api import fetch_via_scrape_api, scrape_api_blocked
from market_parser.stores.variti import (
    parse_onlinetrade,
    parse_perekrestok,
    parse_samokat,
)

CATEGORY = "Детское питание"


def test_parse_samokat_uses_visible_prices_and_skips_overlay() -> None:
    html = """
    <a href="/product/agusha-pyure-115g">
      <div class="ProductCard_name__xyz" title="Агуша пюре яблоко 115г">Агуша пюре</div>
      <div class="Overlay_badge"><span>999 ₽</span></div>
      <span>89 ₽</span><span>129 ₽</span>
    </a>
    """
    products = parse_samokat(html, base_url="https://samokat.ru", category=CATEGORY)
    assert len(products) == 1
    p = products[0]
    assert p.product_name == "Агуша пюре яблоко 115г"
    assert p.regular_price_kopecks == 12900  # max of visible prices
    assert p.promo_price_kopecks == 8900  # min of visible prices
    assert p.product_id == "agusha-pyure-115g"
    assert p.product_url == "https://samokat.ru/product/agusha-pyure-115g"


def test_parse_perekrestok_price_rating_and_id() -> None:
    html = """
    <div class="product-card">
      <a class="product-card__title" href="/cat/230/p/agusa-12345">Агуша каша молочная 200г</a>
      <div class="rating-value">4,8</div>
      <span>119,90 ₽</span><span>149 ₽</span><span>59 ₽ / шт</span>
    </div>
    """
    products = parse_perekrestok(html, base_url="https://www.perekrestok.ru", category=CATEGORY)
    assert len(products) == 1
    p = products[0]
    assert p.regular_price_kopecks == 14900
    assert p.promo_price_kopecks == 11990
    assert p.rating == 4.8
    assert p.product_id == "12345"


def test_parse_onlinetrade_itemprops() -> None:
    html = """
    <div class="indexGoods__item">
      <a class="indexGoods__item__image" href="/catalogue/pyure-frutonyanya-p1234567.html"></a>
      <span itemprop="name">ФрутоНяня пюре груша 90г</span>
      <span itemprop="price" content="75">75 руб</span>
      <span itemprop="ratingValue">4.6</span>
      <div data-itemid="1234567"></div>
    </div>
    """
    products = parse_onlinetrade(html, base_url="https://www.onlinetrade.ru", category=CATEGORY)
    assert len(products) == 1
    p = products[0]
    assert p.regular_price_kopecks == 7500
    assert p.rating == 4.6
    assert p.product_id == "1234567"


def test_scrape_api_blocked_detects_servicepipe_stub() -> None:
    stub = (
        '<html><body><div id="id_spinner"></div>'
        '<script src="https://servicepipe.ru/static/checkjs/x.js"></script></body></html>'
    )
    assert scrape_api_blocked(stub) is True
    assert scrape_api_blocked("<html><body>" + "x" * 5000 + "</body></html>") is False


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError("raise_for_status on error")


class _FakeClient:
    """Captures the request and returns a canned Zyte-style response."""

    captured: dict = {}

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, *, json, auth):
        _FakeClient.captured = {"url": url, "json": json, "auth": auth}
        return _FakeResponse(
            200, {"browserHtml": "<html><body>" + "x" * 5000 + "</body></html>"}
        )


def test_zyte_request_shape_and_html_extraction(monkeypatch) -> None:
    monkeypatch.setattr(scrape_api.httpx, "AsyncClient", _FakeClient)
    settings = Settings(
        scrape_api_provider="zyte", scrape_api_key="zkey", scrape_api_country="ru"
    )
    html = asyncio.run(
        fetch_via_scrape_api(
            "https://www.perekrestok.ru/cat/230/detskoe-pitanie",
            settings,
            scroll_steps=5,
        )
    )
    assert html.startswith("<html>")
    cap = _FakeClient.captured
    assert cap["url"] == "https://api.zyte.com/v1/extract"
    assert cap["auth"] == ("zkey", "")
    assert cap["json"]["browserHtml"] is True
    assert cap["json"]["geolocation"] == "RU"
    actions = cap["json"]["actions"]
    assert any(a["action"] == "scrollBottom" for a in actions)


def test_fetch_via_scrape_api_requires_key() -> None:
    with pytest.raises(StoreAdapterError):
        asyncio.run(
            fetch_via_scrape_api("https://example.com", Settings(scrape_api_key=None))
        )


def test_variti_stores_auto_only_with_scrape_api_key() -> None:
    without = auto_store_slugs(Settings(scrape_api_key=None))
    with_key = auto_store_slugs(Settings(scrape_api_key="test-key"))
    for slug in ("samokat", "perekrestok", "onlinetrade"):
        assert slug not in without
        assert slug in with_key
