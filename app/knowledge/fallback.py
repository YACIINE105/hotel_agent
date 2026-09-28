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
    "address": ["address", "where", "location", "located", "map", "directions", "phone", "email", "contact",
                "عنوان", "العنوان", "أين", "وين", "فين", "موقع", "الموقع", "مكان", "لوكيشن", "خريطة", "هاتف", "رقم",
                "adresse", "où", "situé"],
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
        # Arabic attaches prefixes (و and, ب with, ل for, ف so, ك like, ال the) to words: "والمغادرة", "الإفطار".
        hits = sum(1 for kw in KEYWORDS.get(key, [])
                   if re.search(rf"(?<!\w)(?:[وبلفك]?ال|[وبلفك])?{re.escape(kw.casefold())}(?!\w)", text))
        if hits:
            scored.append((hits, fact))
    scored.sort(key=lambda x: -x[0])
    return [f for _, f in scored[:limit]]

# Other common fact-key spellings used by hotels.
KEYWORDS["pool"] = KEYWORDS["pools"]
KEYWORDS["breakfast"] = KEYWORDS["dining"]


SMALL_TALK = [
    (re.compile(r"\b(thanks?|thank you|thx|merci)\b|شكرا|شكراً|مشكور|يعطيك العافية", re.I), {
        "en": "You're welcome! Anything else I can help with?",
        "ar": "العفو! هل يمكنني مساعدتك في شيء آخر؟",
        "fr": "Avec plaisir ! Puis-je vous aider pour autre chose ?"}),
    (re.compile(r"^\W*(ok(ay)?|alright|good|great|perfect|d'accord)\W*$|^\W*(تمام|حسنا|حسناً|طيب|ممتاز|اوكي)\W*$", re.I), {
        "en": "Great. Let me know if you need anything else.",
        "ar": "تمام. أخبرني إذا احتجت أي شيء آخر.",
        "fr": "Parfait. Dites-moi si vous avez besoin d'autre chose."}),
    (re.compile(r"^\W*(hi|hello|hey|good (morning|evening|afternoon)|bonjour|bonsoir|salut)\b|مرحبا|أهلا|اهلا|السلام عليكم|صباح الخير|مساء الخير", re.I), {
        "en": "Hello! How can I help you with your stay?",
        "ar": "أهلاً بك! كيف يمكنني مساعدتك في إقامتك؟",
        "fr": "Bonjour ! Comment puis-je vous aider pour votre séjour ?"}),
    (re.compile(r"\b(bye|goodbye|see you|au revoir)\b|مع السلامة|وداعا|إلى اللقاء", re.I), {
        "en": "Goodbye, and enjoy your stay!",
        "ar": "مع السلامة، ونتمنى لك إقامة ممتعة!",
        "fr": "Au revoir et bon séjour !"}),
]


def small_talk(question: str) -> dict | None:
    """Localized reply table for greetings/thanks/ok/bye, or None."""
    for pattern, replies in SMALL_TALK:
        if pattern.search(question.strip()):
            return replies
    return None


FREE_CLAIM = re.compile(
    r"\b(free|complimentary|included|no charge|at no cost|gratuit\w*|inclus\w*|offert\w*)\b"
    r"|مجان|مجانا|مجاناً|مجاني|مجانية|بدون تكلفة|بدون رسوم|مشمول",
    re.IGNORECASE,
)


def unsupported_free_claim(answer: str, question: str, facts: list[dict]) -> list[dict] | None:
    """If the answer says something is free/included but the matching approved facts don't,
    return those facts (to answer with instead). None when the claim is supported or absent."""
    if not FREE_CLAIM.search(answer):
        return None
    matched = match_facts(question + " " + answer, facts, limit=2)
    if not matched or any(FREE_CLAIM.search(f["content"]) for f in matched):
        return None
    return matched
