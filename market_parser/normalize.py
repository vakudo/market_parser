from __future__ import annotations

import hashlib
import re
import unicodedata
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

SPACE_TRANSLATION = str.maketrans(
    {
        "\u00a0": " ",
        "\u2007": " ",
        "\u2009": " ",
        "\u202f": " ",
        "\u2006": " ",
        "\u2060": "",
    }
)

PRICE_RE = re.compile(r"\d[\d\s.,]*")

KNOWN_BRANDS = [
    "Nestle HealthScience",
    "Бабушкино Лукошко",
    "Когда я вырасту",
    "Черноголовка Бэйби",
    "Черноголовка",
    "Святой Источник",
    "Маленькое счастье",
    "Сады Придонья",
    "Fleur Alpine",
    "Флёр Альпин",
    "ФрутоНяня",
    "Nutrilon",
    "Nestogen",
    "Nestle",
    "Gerber",
    "Heinz",
    "Агуша",
    "NAN",
    "HiPP",
    "Semper",
    "Kabrita",
    "Беллакт",
    "Малютка",
    "Тёма",
    "Тема",
    "Fleur Alpine",
    "Humana",
    "Bebi",
    "Similac",
    "Мамако",
    "Nutrilak",
    "Friso",
    "Сады Придонья",
    "BabyGo",
    "Bebivita",
    "Neocate",
    "Gipopo",
    "Бибиколь",
    "Bekari",
    "BEKARI",
    "Vulcanica",
    "VULCANICA BABY",
    "Айдиго",
    "Amarancho",
    "Hydroniq",
    "Pasta la Bella",
    "LateMa",
    "Дары Кубани",
    "Сладкая сказка",
    "Фанни Ямми",
    "Егор Иваныч",
    "Ам-Ам",
    "Фиксики",
    "Ладушки",
    "ЛАДУШКИ",
    "Малоежка",
    "Goattiny",
    "MAMAKO",
    "KOZЯ",
    "Фрумка",
    "Mamelle",
    "Resource",
    "PEPTAMEN",
    "Optifast",
    "Нутрилак",
    "Нутриция",
    "Малышам",
    "Малыш",
    "Фитоша",
    "Винни",
    "Фруто",
    "V.O.D.A.",
    "Растишка",
    "Калинов Родничок",
    "Каспер",
    "Бонди",
    "Здоровейка",
    "Нутрини",
    "Nutrinidrink",
    "Nutridrink",
    "Actimuno",
    "Имунеле",
    "Нэнни",
    "Take a Bite",
    "Take",
    "Bergen",
    "ФЯ",
    "Freddi",
    "Kinder",
    "Choco Pie",
    "ORION",
    "Конфитрейд",
    "Профессор Травкин",
]

GENERIC_LEADING_WORDS = {
    "батончик",
    "безалкогольный",
    "безмолочная",
    "бисквитное",
    "белковый",
    "бананом",
    "в",
    "вафли",
    "витамины-кальций",
    "вкусом",
    "вода",
    "воздушный",
    "готовая",
    "готовое",
    "готовый",
    "восстанавливающий",
    "газированный",
    "гранола",
    "груша",
    "десерт",
    "детская",
    "детские",
    "детский",
    "детское",
    "детей",
    "для",
    "диетическое",
    "диетического",
    "звездочки",
    "каша",
    "кашка",
    "кисель",
    "кисломолочный",
    "коктейль",
    "компот",
    "консервы",
    "кукурузные",
    "кусочки",
    "лечебное",
    "из",
    "и",
    "лавки",
    "макароны",
    "мини-пирожные",
    "молочная",
    "молочные",
    "молочко",
    "молочный",
    "морс",
    "напиток",
    "нектар",
    "малины",
    "малиной",
    "новогоднее",
    "на",
    "быстрого",
    "печенье",
    "палочки",
    "пастила",
    "пастилки",
    "пирожное",
    "питание",
    "приготовления",
    "продукт",
    "продукты",
    "рисовые",
    "раннего",
    "с",
    "подушечки",
    "полезный",
    "пюре",
    "сливочное",
    "смесь",
    "со",
    "сбалансированное",
    "специализированная",
    "специализированное",
    "сок",
    "сухая",
    "сухой",
    "творог",
    "хлебцы",
    "чипсы",
    "энтеральное",
    "яблока",
    "яблоко-киви-банан",
    "йогурт",
    "фиточай",
    "фреш",
    "фрикадельки",
    "фруктовая",
    "фруктовые",
    "хлебные",
    "чай",
    "чоко",
    "чоко-пай",
    "шарики",
}


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", value).translate(SPACE_TRANSLATION)
    return re.sub(r"\s+", " ", text).strip()


def parse_price_to_kopecks(value: str | int | float | Decimal | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value * 100
    if isinstance(value, float):
        return _decimal_to_kopecks(Decimal(str(value)))
    if isinstance(value, Decimal):
        return _decimal_to_kopecks(value)

    text = normalize_text(str(value))
    if not text:
        return None
    matches = extract_price_values(text)
    return matches[0] if matches else None


def wb_units_to_kopecks(value: int | None) -> int | None:
    """Wildberries API returns prices in kopecks-like integer units."""
    if value is None:
        return None
    return int(value)


def extract_price_values(text: str) -> list[int]:
    normalized = normalize_text(text)
    values: list[int] = []
    for match in PRICE_RE.findall(normalized):
        token = match.strip(" .,")
        if not token:
            continue
        parsed = _parse_price_token(token)
        if parsed is not None and parsed > 0:
            values.append(parsed)
    return values


def _parse_price_token(token: str) -> int | None:
    token = token.replace(" ", "")
    if not token:
        return None

    decimal_sep = None
    if "," in token and "." in token:
        decimal_sep = "," if token.rfind(",") > token.rfind(".") else "."
    elif "," in token:
        decimal_sep = "," if len(token.rsplit(",", 1)[1]) in {1, 2} else None
    elif "." in token:
        decimal_sep = "." if len(token.rsplit(".", 1)[1]) in {1, 2} else None

    if decimal_sep:
        whole, fraction = token.rsplit(decimal_sep, 1)
        whole = re.sub(r"\D", "", whole)
        fraction = re.sub(r"\D", "", fraction)[:2].ljust(2, "0")
        number = f"{whole}.{fraction}"
    else:
        number = re.sub(r"\D", "", token)

    if not number:
        return None
    try:
        return _decimal_to_kopecks(Decimal(number))
    except InvalidOperation:
        return None


def _decimal_to_kopecks(value: Decimal) -> int:
    return int((value * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def choose_price_fields(
    prices: list[int],
    *,
    loyalty_context: bool = False,
) -> tuple[int | None, int | None, int | None]:
    unique_prices = list(dict.fromkeys(prices))
    if not unique_prices:
        return None, None, None
    if len(unique_prices) == 1:
        return unique_prices[0], None, None

    if loyalty_context:
        loyalty = min(unique_prices)
        regular = max(unique_prices)
        promo = None
        middle = [price for price in unique_prices if price not in {loyalty, regular}]
        if middle:
            promo = min(middle)
        return regular, promo, loyalty

    regular = max(unique_prices)
    promo = min(unique_prices)
    if regular == promo:
        promo = None
    return regular, promo, None


def guess_brand(product_name: str) -> str:
    name = normalize_text(product_name)
    if not name:
        return ""
    folded = name.casefold()
    for brand in sorted(KNOWN_BRANDS, key=len, reverse=True):
        if brand.casefold() in folded:
            return brand
    tokens = re.findall(r"[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9-]*", name)
    for token in tokens:
        cleaned = token.strip("«»\"'.,")
        if cleaned.casefold() in GENERIC_LEADING_WORDS:
            continue
        if _looks_like_brand_token(cleaned):
            return cleaned
    return ""


def _looks_like_brand_token(token: str) -> bool:
    return any(ch.isupper() for ch in token)


def product_key(store_slug: str, product_id: str | None, product_url: str | None, name: str) -> str:
    if product_id:
        return f"{store_slug}:{product_id}"
    seed = "|".join([store_slug, normalize_text(product_url or ""), normalize_text(name).lower()])
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:20]
    return f"{store_slug}:sha1:{digest}"


def kopecks_to_rubles(value: int | None) -> float | None:
    if value is None:
        return None
    return value / 100
