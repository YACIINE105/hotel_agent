"""Demo property: Steigenberger ALDAU Beach Hotel, Hurghada (listed on Booking.com).

Hotel facts below come from the hotel's official page (hrewards.com/en/steigenberger-aldau-beach-hotel-hurghada,
retrieved 2026-09-28). Availability and prices are SIMULATED by the fake connector: no live connection to the
hotel's systems exists. This is an independent demo, not affiliated with the hotel.
"""

import json
import pathlib

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.knowledge.service import KnowledgeService
from app.models import KnowledgeChunk, Organization, Property
from app.security import hash_api_key

DEMO_KEY = "demo-aldau-staff-key"
SLUG = "steigenberger-aldau"

# Room names, sizes, and occupancy from the official page; nightly rates are simulated.
ROOMS = [
    {"code": "SUP-GP", "name": "Superior Suite, Garden/Pool View", "size": "50 m²", "max": 3, "max_adults": 3,
     "rate": "190.00", "count": 6, "meal": "Breakfast included (simulated rate)"},
    {"code": "SUP-SEA", "name": "Superior Suite, Sea View", "size": "50 m²", "max": 3, "max_adults": 2,
     "rate": "235.00", "count": 4, "meal": "Breakfast included (simulated rate)"},
    {"code": "FAM", "name": "Family Suite", "size": "55 m²", "max": 4, "max_adults": 3,
     "rate": "280.00", "count": 2, "meal": "Breakfast included (simulated rate)"},
]

# Official children policy: under 6 free using existing beds; 6-12 charged 32 USD per night; 12+ are adults.
CHILD_POLICY = {"child_free_under": 6, "child_adult_from": 12, "child_fee_per_night": "32.00"}

FACTS = {
    "check_in": {
        "en": "Check-in is from 15:00.",
        "ar": "تسجيل الوصول ابتداءً من الساعة 15:00.",
    },
    "check_out": {
        "en": "Check-out is until 12:00.",
        "ar": "تسجيل المغادرة حتى الساعة 12:00.",
    },
    "address": {
        "en": "Steigenberger ALDAU Beach Hotel, Youssif Afifi Road, Hurghada, Red Sea, Egypt. "
              "Phone +20 65 3465 400, email ebookings@steigenbergeraldau.com.",
        "ar": "فندق شتيجنبرجر الداو بيتش، طريق يوسف عفيفي، الغردقة، البحر الأحمر، مصر. "
              "الهاتف ‎+20 65 3465 400، البريد الإلكتروني ebookings@steigenbergeraldau.com.",
    },
    "rooms": {
        "en": "The hotel has 400 suites. Superior Suites are 50 m² with garden, pool, or sea view; Family Suites are "
              "55 m² for 3 adults or 2 adults and 2 children; larger suites include the Junior Suite (74 m²) and the "
              "Deluxe Suite (142 m²) with two balconies.",
        "ar": "يضم الفندق 400 جناح. الأجنحة السوبيريور بمساحة 50 م² مع إطلالة على الحديقة أو المسبح أو البحر، "
              "والأجنحة العائلية بمساحة 55 م² تتسع لثلاثة بالغين أو بالغين وطفلين، ومن الأجنحة الأكبر الجناح "
              "الجونيور (74 م²) والجناح الديلوكس (142 م²) مع شرفتين.",
    },
    "dining": {
        "en": "There are four restaurants, five bars, a café, and a traditional shisha bar. Room service is available "
              "24 hours.",
        "ar": "يضم الفندق أربعة مطاعم وخمسة بارات ومقهى وركناً تقليدياً للشيشة. خدمة الغرف متاحة على مدار 24 ساعة.",
    },
    "pools": {
        "en": "The hotel has a 5,000 m² outdoor pool landscape with a current channel, plus an indoor heated pool in "
              "the Pure Spa.",
        "ar": "يضم الفندق مسابح خارجية بمساحة 5000 م² مع قناة تيار مائي، بالإضافة إلى مسبح داخلي مُدفأ في بيور سبا.",
    },
    "spa": {
        "en": "The Pure Spa has an indoor heated pool, a hamam, a sauna, and extensive wellness facilities.",
        "ar": "يضم بيور سبا مسبحاً داخلياً مُدفأ وحماماً تركياً وساونا ومرافق عافية متكاملة.",
    },
    "beach": {
        "en": "The hotel is directly on a private sandy beach on the Red Sea, with barrier-free ramp access to the "
              "beach.",
        "ar": "يقع الفندق مباشرة على شاطئ رملي خاص على البحر الأحمر، مع منحدرات تتيح الوصول إلى الشاطئ دون عوائق.",
    },
    "golf": {
        "en": "The hotel has a 9-hole par-3 golf course.",
        "ar": "يضم الفندق ملعب جولف من 9 حفر (بار 3).",
    },
    "water_sports": {
        "en": "There is a water sports and diving center at the hotel.",
        "ar": "يوجد في الفندق مركز للرياضات المائية والغوص.",
    },
    "children": {
        "en": "Children under 6 stay free in their parents' room using existing beds. Children aged 6 to 12 are "
              "charged 32 USD per night. Guests aged 12 and above count as adults. A kids club is available.",
        "ar": "يقيم الأطفال دون 6 سنوات مجاناً في غرفة الوالدين باستخدام الأسرّة الموجودة. يُحتسب على الأطفال من 6 "
              "إلى 12 سنة 32 دولاراً في الليلة. يُعتبر من هم في سن 12 عاماً فأكثر بالغين. يتوفر نادٍ للأطفال.",
    },
    "parking": {
        "en": "Parking is directly at the hotel and free of charge.",
        "ar": "موقف السيارات متاح مباشرة في الفندق ومجاناً.",
    },
    "wifi": {
        "en": "High-speed Wi-Fi is free.",
        "ar": "خدمة الواي فاي عالية السرعة مجانية.",
    },
    "pets": {
        "en": "Pets are not allowed.",
        "ar": "لا يُسمح باصطحاب الحيوانات الأليفة.",
    },
    "accessibility": {
        "en": "Four rooms are equipped for guests with disabilities, and ramps give barrier-free access to the beach.",
        "ar": "تتوفر أربع غرف مجهزة لذوي الاحتياجات الخاصة، ومنحدرات للوصول إلى الشاطئ دون عوائق.",
    },
    "airport_transfer": {
        "en": "The hotel offers a shuttle service. Please ask the front desk for airport transfer times and prices.",
        "ar": "يوفر الفندق خدمة نقل. يرجى سؤال مكتب الاستقبال عن مواعيد وأسعار النقل من المطار وإليه.",
    },
    "meetings": {
        "en": "There are seven meeting rooms for up to 1,200 people.",
        "ar": "يضم الفندق سبع قاعات اجتماعات تتسع حتى 1200 شخص.",
    },
}


AREA_GUIDE = pathlib.Path(__file__).resolve().parents[1] / "data" / "area_guide"
AREA_SOURCE_PREFIX = "Area guide ·"


async def ensure_area_guide(session: AsyncSession, prop: Property) -> int:
    """Load the Hurghada area guide (Wikipedia, CC BY-SA 4.0) as retrievable documents, once."""
    exists = await session.scalar(select(KnowledgeChunk.id).where(
        KnowledgeChunk.property_id == prop.id, KnowledgeChunk.source.like(f"{AREA_SOURCE_PREFIX}%")).limit(1))
    if exists or not AREA_GUIDE.is_dir():
        return 0
    knowledge = KnowledgeService(session, None, prop.id)
    total = 0
    for path in sorted(AREA_GUIDE.glob("*.json")):
        doc = json.loads(path.read_text())
        source = f"{AREA_SOURCE_PREFIX} {doc['title']} (Wikipedia, CC BY-SA 4.0, {doc['url']})"
        total += await knowledge.ingest(source, doc["text"])
    await session.commit()
    return total


async def seed(session: AsyncSession) -> None:
    existing = await session.scalar(select(Property).where(Property.slug == SLUG))
    if existing:
        await ensure_area_guide(session, existing)
        return
    if await session.scalar(select(Property).limit(1)):
        return
    org = Organization(name="Steigenberger ALDAU Beach Hotel (demo)", api_key_hash=hash_api_key(DEMO_KEY))
    session.add(org)
    await session.flush()
    prop = Property(
        org_id=org.id, slug=SLUG, name="Steigenberger ALDAU Beach Hotel", timezone="Africa/Cairo",
        currency="USD", languages=["en", "ar"],
        connector={"type": "fake", "rooms": ROOMS, "city_tax_per_adult_night": "0", **CHILD_POLICY},
    )
    session.add(prop)
    await session.flush()
    knowledge = KnowledgeService(session, None, prop.id)
    for key, by_lang in FACTS.items():
        for language, content in by_lang.items():
            await knowledge.upsert_fact(key, language, content)
    await session.commit()
    await ensure_area_guide(session, prop)
