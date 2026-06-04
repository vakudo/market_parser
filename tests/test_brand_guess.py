from market_parser.normalize import guess_brand


def test_guess_brand_skips_category_words() -> None:
    assert guess_brand("Пюре BabyGo Банан-груша-яблоко с 6 мес 100 г") == "BabyGo"
    assert guess_brand("Каша BabyGo молочная 5 злаков, фрукты") == "BabyGo"
    assert guess_brand("Смесь Neocate Syneo Аминокислоты 400г c 0месяцев") == "Neocate"
    assert guess_brand("Печенье BabyGo растворимое из 5 злаков") == "BabyGo"
    assert guess_brand("Пюре Bebivita Овощное рагу с цыплёнком") == "Bebivita"
    assert guess_brand("Смесь на основе козьего молока Goattiny 4") == "Goattiny"
    assert guess_brand("Пюре из яблок и персиков с козьим творогом MAMAKO") == "MAMAKO"
