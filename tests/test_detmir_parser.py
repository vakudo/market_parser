from market_parser.stores.html_extractors import parse_detmir_cards


def test_parse_detmir_cards_regular_and_promo() -> None:
    html = """
    <section data-product-id="3101507">
      <div data-testid="productPrice">
        <span class="diTAa uqCzd">3 269 ₽</span>
        <span class="iwVsa">3 869 ₽</span>
      </div>
      <a href="https://www.detmir.ru/product/index/id/3101507/" data-testid="titleLink">
        <span>Смесь сухая Nutrilon Пепти Аллергия 800г с 0 месяцев</span>
      </a>
    </section>
    """

    products = parse_detmir_cards(html, category="Детское питание", limit=10)

    assert len(products) == 1
    product = products[0]
    assert product.product_id == "3101507"
    assert product.brand == "Nutrilon"
    assert product.regular_price_kopecks == 386900
    assert product.promo_price_kopecks == 326900
