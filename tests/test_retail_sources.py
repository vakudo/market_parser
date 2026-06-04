from market_parser.stores.html_extractors import parse_generic_product_cards
from market_parser.stores.retail_sources import parse_markdown_products


def test_generic_parser_handles_ruble_symbol_near_product_link() -> None:
    html = """
    <article class="card">
      <a href="/product/pyure-agusha-90" title="Пюре фруктовое Агуша Банан с 6 месяцев 90г">
        Пюре фруктовое Агуша Банан с 6 месяцев 90г
      </a>
      <span>57,99 ₽</span>
      <span>42,99 ₽</span>
    </article>
    """

    products = parse_generic_product_cards(
        html,
        store_slug="pyaterochka",
        store_name="Пятёрочка",
        category="Детское питание",
        base_url="https://5ka.ru",
        product_href_patterns=(r"/product/",),
        limit=10,
    )

    assert len(products) == 1
    assert products[0].brand == "Агуша"
    assert products[0].regular_price_kopecks == 5799
    assert products[0].promo_price_kopecks == 4299


def test_markdown_parser_extracts_compilation_item() -> None:
    text = """
    # Пюре фрутоняня ассортимент

    * [Пюре ФрутоНяня Фруктовый салатик 90 г](/good/frutonyanya-salatik)
    69 ₽ вместо обычной цены 99 ₽
    """

    products = parse_markdown_products(
        text,
        store_slug="yandex_lavka",
        store_name="Яндекс.Лавка",
        category="Детское питание",
        base_url="https://lavka.yandex.ru/compilations/test",
        limit=10,
    )

    assert len(products) == 1
    assert products[0].product_url == "https://lavka.yandex.ru/good/frutonyanya-salatik"
    assert products[0].regular_price_kopecks == 9900
    assert products[0].promo_price_kopecks == 6900
