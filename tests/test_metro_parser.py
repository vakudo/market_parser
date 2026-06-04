from market_parser.stores.metro import parse_metro_cards


def test_parse_metro_cards_regular_and_promo() -> None:
    html = """
    <div class="product-card" data-sku="566299">
      <span class="product-price product-unit-prices__actual">
        <span class="product-price__sum-rubles">51</span>
        <span class="product-price__sum-penny">.90</span>
      </span>
      <span class="product-price product-unit-prices__old">
        <span class="product-price__sum-rubles">67</span>
        <span class="product-price__sum-penny">.90</span>
      </span>
      <a class="product-card-name" href="/products/item-566299"
         title="Вода Черноголовка питьевая негазированная, 1.5л"></a>
    </div>
    """

    products = parse_metro_cards(html, category="Детское питание", limit=10)

    assert len(products) == 1
    assert products[0].brand == "Черноголовка"
    assert products[0].regular_price_kopecks == 6790
    assert products[0].promo_price_kopecks == 5190
