"""Two demo hotels in different organizations, so isolation is visible in the demo."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.knowledge.service import KnowledgeService
from app.models import Organization, Property
from app.security import hash_api_key

DEMO_KEYS = {"atlas": "demo-atlas-staff-key", "oran": "demo-oran-staff-key"}

ATLAS_ROOMS = [
    {"code": "STD", "name": "Standard Double Room", "max": 2, "rate": "12000.00", "count": 5, "meal": "Breakfast included"},
    {"code": "SEA", "name": "Sea View Double Room", "max": 3, "rate": "18500.00", "count": 2, "meal": "Breakfast included"},
    {"code": "FAM", "name": "Family Suite", "max": 4, "rate": "26000.00", "count": 1, "meal": "Breakfast included"},
]

ATLAS_FACTS = {
    "check_in": {
        "en": "Check-in is from 14:00. Late check-in is available 24 hours a day at the front desk; please tell us your arrival time.",
        "fr": "L'enregistrement se fait à partir de 14h00. L'arrivée tardive est possible 24h/24 à la réception ; merci de nous indiquer votre heure d'arrivée.",
        "ar": "تسجيل الوصول من الساعة 14:00. يمكن الوصول المتأخر على مدار 24 ساعة في مكتب الاستقبال، يرجى إعلامنا بموعد وصولك.",
    },
    "check_out": {
        "en": "Check-out is by 12:00. Late check-out until 16:00 costs 3,000 DZD, subject to availability.",
        "fr": "Le départ se fait avant 12h00. Un départ tardif jusqu'à 16h00 coûte 3 000 DZD, selon disponibilité.",
        "ar": "تسجيل المغادرة قبل الساعة 12:00. المغادرة المتأخرة حتى 16:00 بتكلفة 3000 دج حسب التوفر.",
    },
    "breakfast": {
        "en": "Breakfast is served 06:30-10:30 in the Terrace restaurant and is included in all room rates.",
        "fr": "Le petit-déjeuner est servi de 6h30 à 10h30 au restaurant La Terrasse et il est inclus dans tous les tarifs.",
        "ar": "يقدم الفطور من 06:30 إلى 10:30 في مطعم التراس وهو مشمول في جميع الأسعار.",
    },
    "parking": {"en": "Free private parking on site; no reservation needed. EV charging is not available."},
    "wifi": {"en": "Free high-speed Wi-Fi throughout the hotel."},
    "pool": {"en": "Outdoor pool open May to October, 08:00-19:00. Towels provided."},
    "airport_transfer": {"en": "Airport transfer from Algiers airport costs 4,000 DZD per car (up to 3 guests). Book at least 24 hours ahead."},
    "pets": {"en": "Pets are not allowed, except assistance dogs."},
    "children": {"en": "Children under 6 stay free using existing beds. Cots are free on request. The Family Suite fits 2 adults and 2 children."},
    "address": {"en": "Atlas Bay Hotel, Boulevard du Front de Mer, Algiers. 25 minutes from the airport."},
    "payment_methods": {"en": "We accept cash (DZD), CIB/Edahabia cards, Visa, and Mastercard at the property."},
    "accessibility": {"en": "Two ground-floor accessible rooms with roll-in showers; elevator to all floors."},
}

ORAN_FACTS = {
    "check_in": {"en": "Check-in at Oran Medina Suites is from 15:00; late arrivals after 23:00 must call ahead."},
    "breakfast": {"en": "Breakfast costs 1,500 DZD per person, served 07:00-10:00."},
    "parking": {"en": "No on-site parking; public parking is 200 m away."},
}


async def seed(session: AsyncSession) -> None:
    if await session.scalar(select(Property).limit(1)):
        return
    atlas_org = Organization(name="Atlas Hospitality", api_key_hash=hash_api_key(DEMO_KEYS["atlas"]))
    oran_org = Organization(name="Oran Medina Group", api_key_hash=hash_api_key(DEMO_KEYS["oran"]))
    session.add_all([atlas_org, oran_org])
    await session.flush()
    atlas = Property(org_id=atlas_org.id, slug="atlas-bay", name="Atlas Bay Hotel", timezone="Africa/Algiers",
                     currency="DZD", languages=["en", "ar", "fr"], connector={"type": "fake", "rooms": ATLAS_ROOMS})
    oran = Property(org_id=oran_org.id, slug="oran-medina", name="Oran Medina Suites", timezone="Africa/Algiers",
                    currency="DZD", languages=["en", "fr"], connector={"type": "fake", "rooms": ATLAS_ROOMS[:1]})
    session.add_all([atlas, oran])
    await session.flush()
    for prop, facts in ((atlas, ATLAS_FACTS), (oran, ORAN_FACTS)):
        knowledge = KnowledgeService(session, None, prop.id)
        for key, by_lang in facts.items():
            for language, content in by_lang.items():
                await knowledge.upsert_fact(key, language, content)
    await KnowledgeService(session, None, atlas.id).ingest(
        "Local guide",
        "The Casbah of Algiers, a UNESCO site, is 20 minutes away by taxi. The hotel concierge can book a guided tour.\n\n"
        "The Jardin d'Essai botanical garden is 10 minutes away and opens 09:00-18:00.\n\n"
        "For dinner, the hotel's Terrace restaurant serves Algerian and Mediterranean dishes 19:00-23:00.",
    )
    await session.commit()
