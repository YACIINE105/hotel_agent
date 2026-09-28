"""Answer common questions from approved facts when the language model is unavailable.

Keyword matching per fact key (English, Arabic, French). It only returns approved hotel text,
so it cannot invent anything; unmatched questions still get the "team can help" message.
"""

import re

KEYWORDS: dict[str, list[str]] = {
    "check_in": ["check-in", "check in", "checkin", "arrival", "arrive", "وصول", "الوصول", "تسجيل الدخول", "arrivée", "enregistrement"],
    "check_out": ["check-out", "check out", "checkout", "leave", "departure", "مغادرة", "المغادرة", "départ"],
    "pools": ["pool", "pools", "swim", "مسبح", "المسبح", "مسابح", "سباحة", "piscine"],
    "spa": ["spa", "sauna", "hamam", "hammam", "massage", "wellness", "سبا", "ساونا", "حمام تركي", "مساج"],
    "parking": ["parking", "park", "car", "موقف", "مواقف", "سيارة", "stationnement"],
    "wifi": ["wifi", "wi-fi", "internet", "واي فاي", "انترنت", "إنترنت"],
    "pets": ["pet", "pets", "dog", "cat", "حيوان", "حيوانات", "كلب", "animaux", "chien"],
    "dining": ["restaurant", "restaurants", "bar", "food", "eat", "dinner", "lunch", "breakfast", "room service",
               "مطعم", "مطاعم", "طعام", "فطور", "إفطار", "عشاء", "غداء", "petit-déjeuner", "dîner"],
    "beach": ["beach", "sea", "شاطئ", "الشاطئ", "بحر", "plage", "mer"],
    "golf": ["golf", "جولف", "غولف"],
    "water_sports": ["diving", "dive", "snorkel", "water sport", "غوص", "رياضات مائية", "plongée"],
    "children": ["child", "children", "kid", "kids", "baby", "طفل", "أطفال", "الأطفال", "enfant", "enfants"],
    "address": ["address", "where", "location", "phone", "email", "contact", "عنوان", "العنوان", "أين", "هاتف", "adresse"],
    "airport_transfer": ["airport", "transfer", "shuttle", "taxi", "مطار", "المطار", "نقل", "aéroport", "navette"],
    "accessibility": ["wheelchair", "disabled", "accessible", "accessibility", "كرسي متحرك", "احتياجات خاصة", "handicap"],
    "rooms": ["suite", "suites", "room types", "rooms", "أجنحة", "جناح", "غرف", "chambres"],
    "meetings": ["meeting", "conference", "event", "اجتماع", "مؤتمر", "réunion", "conférence"],
}


def match_facts(question: str, facts: list[dict], limit: int = 2) -> list[dict]:
    """facts: [{"id": "F:key", "content": ...}] already localized. Returns the best matches."""
    text = question.casefold()
    scored = []
    for fact in facts:
        key = fact["id"].split(":", 1)[1]
        # Arabic attaches prefixes (و and, ب with, ل for, ف so, ك like) directly to words: "والمغادرة".
        hits = sum(1 for kw in KEYWORDS.get(key, [])
                   if re.search(rf"(?<!\w)(?:[وبلفك])?{re.escape(kw.casefold())}(?!\w)", text))
        if hits:
            scored.append((hits, fact))
    scored.sort(key=lambda x: -x[0])
    return [f for _, f in scored[:limit]]

# Other common fact-key spellings used by hotels.
KEYWORDS["pool"] = KEYWORDS["pools"]
KEYWORDS["breakfast"] = KEYWORDS["dining"]
