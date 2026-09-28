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


NOT_UNDERSTOOD = {
    "en": "Sorry, I didn't catch that. Could you say it again?",
    "ar": "عذراً، لم أفهم ذلك جيداً. هل يمكنك الإعادة؟",
    "fr": "Désolé, je n'ai pas bien compris. Pouvez-vous répéter ?",
}


def _script(ch: str) -> str | None:
    code = ord(ch)
    if code < 0x250:
        return "latin"
    if 0x600 <= code <= 0x6FF or 0x750 <= code <= 0x77F or 0xFB50 <= code <= 0xFEFF:
        return "arabic"
    return None


def plausible_transcript(text: str, languages: list[str]) -> bool:
    """ASR on background noise often yields short text in a random language (Thai, Chinese...).

    Accept only transcripts with enough letters, mostly in scripts the hotel's languages use.
    """
    allowed = {"arabic" if code == "ar" else "latin" for code in languages} | {"latin"}
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 3:
        return False
    return sum(_script(c) in allowed for c in letters) / len(letters) >= 0.8


QUOTE_READY = {
    "en": "Here is your booking summary. Please check the details and press Confirm to book.",
    "ar": "هذا ملخص حجزك. يرجى مراجعة التفاصيل ثم الضغط على تأكيد لإتمام الحجز.",
    "fr": "Voici le récapitulatif de votre réservation. Vérifiez les détails puis appuyez sur Confirmer.",
}

HANDED_OFF = {
    "en": "I've passed this to our team; a staff member will reply here shortly.",
    "ar": "لقد حولت طلبك إلى فريقنا، وسيرد عليك أحد الموظفين هنا قريباً.",
    "fr": "J'ai transmis votre demande à notre équipe ; un membre du personnel vous répondra ici rapidement.",
}
