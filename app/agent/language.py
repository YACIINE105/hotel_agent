import re

ARABIC = re.compile(r"[؀-ۿ]")
FRENCH_HINTS = re.compile(
    r"\b(bonjour|bonsoir|merci|chambre|nuit|je|vous|nous|est-ce|disponible|réservation|réserver|"
    r"petit-déjeuner|arrivée|départ|s'il)\b|[éèêàçù]",
    re.IGNORECASE,
)
NAMES = {"en": "English", "ar": "Arabic", "fr": "French"}

STATUS = {
    "search_availability": {
        "en": "Checking live availability…",
        "ar": "جارٍ التحقق من الغرف المتاحة…",
        "fr": "Vérification des disponibilités…",
    },
    "prepare_booking": {
        "en": "Preparing your booking summary…",
        "ar": "جارٍ تجهيز ملخص الحجز…",
        "fr": "Préparation du récapitulatif…",
    },
    "find_reservation": {
        "en": "Looking up your reservation…",
        "ar": "جارٍ البحث عن حجزك…",
        "fr": "Recherche de votre réservation…",
    },
    "handoff_to_staff": {
        "en": "Connecting you with our team…",
        "ar": "جارٍ تحويلك إلى فريقنا…",
        "fr": "Mise en relation avec notre équipe…",
    },
}

UNAVAILABLE = {
    "en": "Sorry, I'm having trouble right now. A member of our team can help you.",
    "ar": "عذراً، أواجه مشكلة حالياً. يمكن لأحد أعضاء فريقنا مساعدتك.",
    "fr": "Désolé, je rencontre un problème. Un membre de notre équipe peut vous aider.",
}

PAUSED = {
    "en": "A member of our team is handling this conversation and will reply shortly.",
    "ar": "يتولى أحد أعضاء فريقنا هذه المحادثة وسيرد عليك قريباً.",
    "fr": "Un membre de notre équipe gère cette conversation et vous répondra bientôt.",
}


def detect(text: str) -> str:
    letters = [c for c in text if c.isalpha()]
    if letters and sum(bool(ARABIC.match(c)) for c in letters) / len(letters) > 0.3:
        return "ar"
    if FRENCH_HINTS.search(text):
        return "fr"
    return "en"


def localized(table: dict, lang: str | None) -> str:
    return table.get(lang or "en", table["en"])
