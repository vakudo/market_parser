from market_parser.stores.magnit import parse_magnit_cards


def test_parse_magnit_cards_regular_and_promo() -> None:
    html = """
    <article class="unit-catalog-product-preview">
      <a href="/product/1000512987-kogda_ya_vyrastu?shopCode=992301"
         title="Фруктовые кусочки Когда Я Вырасту яблоко клубника 30г"></a>
      <div class="unit-catalog-product-preview-prices">
        <span class="unit-catalog-product-preview-prices__regular">46.99 ₽</span>
        <span class="unit-catalog-product-preview-prices__sale">69.99 ₽</span>
      </div>
    </article>
    """

    products = parse_magnit_cards(html, category="Детское питание", limit=10)

    assert len(products) == 1
    assert products[0].brand == "Когда я вырасту"
    assert products[0].regular_price_kopecks == 6999
    assert products[0].promo_price_kopecks == 4699
