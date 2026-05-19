from decimal import Decimal

from market_parser.normalize import (
    choose_price_fields,
    extract_price_values,
    parse_price_to_kopecks,
    product_key,
)


def test_parse_price_to_kopecks_handles_russian_spaces() -> None:
    assert parse_price_to_kopecks("2 017 ₽") == 201700
    assert parse_price_to_kopecks("1 189,50 руб.") == 118950
    assert parse_price_to_kopecks(Decimal("45.25")) == 4525


def test_extract_multiple_prices() -> None:
    assert extract_price_values("3 269 ₽ 3 869 ₽") == [326900, 386900]


def test_choose_price_fields_discount() -> None:
    regular, promo, loyalty = choose_price_fields([326900, 386900])
    assert regular == 386900
    assert promo == 326900
    assert loyalty is None


def test_choose_price_fields_loyalty_context() -> None:
    regular, promo, loyalty = choose_price_fields([169900, 177000], loyalty_context=True)
    assert regular == 177000
    assert promo is None
    assert loyalty == 169900


def test_product_key_prefers_store_product_id() -> None:
    assert product_key("ozon", "123", "https://example.test/a", "Name") == "ozon:123"
    assert product_key("ozon", "", "https://example.test/a", "Name").startswith("ozon:sha1:")
