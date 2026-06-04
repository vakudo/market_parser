from market_parser.stores.html_extractors import parse_yandex_market_cards


def test_parse_yandex_market_cards_uses_embedded_widget_json() -> None:
    html = """
    <div data-baobab-name="productSnippet">
      10 % ПРОМОКОД
      {"widgets":{"@light/ToggleWishlist":{"x":{
        "productId":1159015329,
        "skuId":"4382957723",
        "title":"Детское питание Пюре Semper 4 овоща, 6х100г",
        "price":{"value":"1052","currency":"RUR"},
        "oldPrice":{"value":"1403","currency":"RUR"},
        "isAvailable":true
      }}}}
      <a href="/card/pyure-semper/4382957723" data-auto="snippet-link">Открыть</a>
    </div>
    """

    products = parse_yandex_market_cards(html, category="Детское питание", limit=10)

    assert len(products) == 1
    assert products[0].product_name == "Детское питание Пюре Semper 4 овоща, 6х100г"
    assert products[0].brand == "Semper"
    assert products[0].regular_price_kopecks == 140300
    assert products[0].promo_price_kopecks == 105200
    assert products[0].product_url == "https://market.yandex.ru/card/pyure-semper/4382957723"


def test_parse_yandex_market_cards_scans_multiple_widget_json_blocks() -> None:
    html = """
    <div data-baobab-name="productSnippet">
      {"widgets":{"@marketfront/Gallery":{"x":{"pictures":[]}}},"meta":{"x":{"name":"gallery"}}}
      {"widgets":{"@light/ToggleWishlist":{"x":{
        "productId":5195448711,
        "skuId":"4371372288",
        "title":"Детское питание Пюре Semper фруктовое Яблоко и клубника, с 6 месяцев, 6х90г",
        "price":{"value":594,"currency":"RUR"},
        "isAvailable":true
      }}}}
      <a href="/card/pyure-semper/4371372288" data-auto="snippet-link">Открыть</a>
    </div>
    """

    products = parse_yandex_market_cards(html, category="Детское питание", limit=10)

    assert len(products) == 1
    assert products[0].product_id == "4371372288"
    assert products[0].brand == "Semper"
    assert products[0].regular_price_kopecks == 59400
