from market_parser.config import Settings
from market_parser.stores.wildberries import (
    WildberriesAdapter,
    _is_baby_food_item,
    _products_from_payload,
)


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


def test_wildberries_uses_catalog_subject_endpoint() -> None:
    adapter = WildberriesAdapter(Settings())

    assert adapter.api_url == "https://catalog.wb.ru/catalog/product5/v4/catalog"
    assert adapter._catalog_params(3) == {
        "appType": "1",
        "curr": "rub",
        "dest": "-1257786",
        "sort": "popular",
        "spp": "30",
        "subject": "2638",
        "page": "3",
    }


def test_wildberries_payload_products_from_root_or_data() -> None:
    root_products = [{"id": 1}]
    nested_products = [{"id": 2}]

    assert _products_from_payload({"products": root_products}) == root_products
    assert _products_from_payload({"data": {"products": nested_products}}) == nested_products


def test_wildberries_filters_noisy_food_items() -> None:
    allowed_subject_ids = {"2642", "2645"}

    assert _is_baby_food_item(
        {
            "subjectId": 2645,
            "subjectParentId": 2638,
            "brand": "Сады Придонья",
            "name": "Сок яблоко прямого отжима 0.2л",
        },
        "2638",
        allowed_subject_ids,
    )
    assert _is_baby_food_item(
        {
            "subjectId": 2642,
            "subjectParentId": 2638,
            "brand": "Hipp",
            "name": '"Куриный суп с лапшой", с кусочками, с 12 мес',
        },
        "2638",
        allowed_subject_ids,
    )
    # We now trust WB's own categorisation: anything inside an allowed baby-food
    # subject is kept (recall over precision), so this item is no longer dropped.
    assert _is_baby_food_item(
        {
            "subjectId": 2642,
            "subjectParentId": 2638,
            "brand": "",
            "name": "10 пакетиков тушеной лапши с бараниной",
        },
        "2638",
        allowed_subject_ids,
    )
    # ...but clear non-food (sport nutrition, pet food, sauces) is still excluded.
    assert not _is_baby_food_item(
        {
            "subjectId": 2643,
            "subjectParentId": 2638,
            "brand": "1000 Каталог",
            "name": "Печенье протеиновое SPORTY",
        },
        "2638",
        allowed_subject_ids | {"2643"},
    )
    assert not _is_baby_food_item(
        {
            "subjectId": 2642,
            "subjectParentId": 2638,
            "brand": "",
            "name": "Корм для кошек паучи",
        },
        "2638",
        allowed_subject_ids,
    )
