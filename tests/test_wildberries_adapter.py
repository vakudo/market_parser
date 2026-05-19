from market_parser.config import Settings
from market_parser.stores.wildberries import WildberriesAdapter


def test_wildberries_api_item_to_product() -> None:
    adapter = WildberriesAdapter(Settings())
    item = {
        "id": 327468652,
        "brand": "ФрутоНяня",
        "name": "Каша кукурузная безмолочная, 180г",
        "sizes": [{"price": {"basic": 15900, "product": 10100}}],
        "totalQuantity": 55,
        "supplier": "Wildberries",
        "entity": "каши детские",
    }

    product = adapter._product_from_api_item(item)

    assert product.store_slug == "wildberries"
    assert product.brand == "ФрутоНяня"
    assert product.regular_price_kopecks == 15900
    assert product.promo_price_kopecks == 10100
    assert product.product_url.endswith("/327468652/detail.aspx")
