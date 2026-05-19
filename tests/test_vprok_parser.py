from market_parser.stores.html_extractors import parse_vprok_cards


def test_parse_vprok_cards_uses_product_title_not_rating() -> None:
    html = """
    <article>
      <a href="/product/agusha-agusha-kefir-klassicheskiy-3-2-204g--306991/reviews"
         title="Оценка: 4.9">4.9</a>
      <a href="/product/agusha-agusha-kefir-klassicheskiy-3-2-204g--306991"
         class="UiProductTileMain_longName__29CCd"
         title="Кефир детский Агуша Классический 3.2% с 8 месяцев 204мл">
        Кефир детский Агуша Классический 3.2% с 8 месяцев 204мл
      </a>
      <div class="Purchase_totalPrice__5sEl8">
        <span class="Price_price__QzA8L Price_role_old__r1uT1">54<span>,99 ₽</span></span>
        <span class="Price_price__QzA8L Price_role_discount__l_tpE">49<span>,99 ₽/шт</span></span>
      </div>
    </article>
    """

    products = parse_vprok_cards(html, category="Детское питание", limit=10)

    assert len(products) == 1
    assert products[0].product_name == "Кефир детский Агуша Классический 3.2% с 8 месяцев 204мл"
    assert products[0].brand == "Агуша"
    assert products[0].regular_price_kopecks == 5499
    assert products[0].promo_price_kopecks == 4999
    assert products[0].product_url.endswith("--306991")
