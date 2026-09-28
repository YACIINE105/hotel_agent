"""Download the Hurghada area guide used as retrievable knowledge (and RAG test data).

Source: Wikipedia (text under CC BY-SA 4.0; attribution kept per document). Area information
only: nothing here is presented as a hotel policy. Saved to data/area_guide/<lang>--<slug>.json.

    uv run python scripts/fetch_area_guide.py
"""

import json
import pathlib
import re
import time
from datetime import date

import httpx

OUT = pathlib.Path(__file__).resolve().parents[1] / "data" / "area_guide"
ARTICLES = {
    "en": ["Hurghada", "El Gouna", "Hurghada International Airport", "Makadi Bay", "Sahl Hasheesh",
           "Red Sea Riviera", "Soma Bay", "Hurghada Grand Aquarium", "Giftun Islands", "Safaga",
           "Luxor", "Visa policy of Egypt", "Egyptian pound", "Coral reef"],
    "ar": ["الغردقة", "الجونة", "مطار الغردقة الدولي", "سهل حشيش", "سفاجا", "الأقصر", "الجنيه المصري"],
}


def fetch(c, lang, title):
    """Wikipedia asks API clients to go slowly: pause between calls, back off on refusals."""
    for attempt in range(5):
        time.sleep(1.5 * (attempt + 1))
        r = c.get(f"https://{lang}.wikipedia.org/w/api.php", params={
            "action": "query", "prop": "extracts|info", "explaintext": 1, "titles": title,
            "format": "json", "redirects": 1, "inprop": "url"})
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("application/json"):
            return next(iter(r.json()["query"]["pages"].values()))
    raise RuntimeError(f"Wikipedia refused {lang}:{title} (HTTP {r.status_code})")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": "hotel-agent-demo/0.1 (area guide fetch)"}
    with httpx.Client(timeout=60, headers=headers) as c:
        for lang, titles in ARTICLES.items():
            for title in titles:
                done = [p for p in OUT.glob(f"{lang}--*.json") if json.loads(p.read_text())["title"] == title]
                if done:
                    continue  # already fetched
                try:
                    page = fetch(c, lang, title)
                except RuntimeError as exc:
                    print(f"skip: {exc}")
                    continue
                text = (page.get("extract") or "").strip()
                if len(text) < 300:
                    print(f"skip {lang}:{title} (no text)")
                    continue
                # Drop reference-only sections.
                text = re.split(r"\n==+ (See also|References|External links|Notes|Further reading|انظر أيضًا|المراجع|وصلات خارجية|مراجع) ==+", text)[0]
                slug = re.sub(r"\W+", "-", page["title"]).strip("-").lower()
                doc = {"title": page["title"], "language": lang, "url": page.get("fullurl"),
                       "license": "CC BY-SA 4.0 (Wikipedia contributors)", "retrieved": date.today().isoformat(),
                       "text": text}
                (OUT / f"{lang}--{slug}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1))
                print(f"{lang}:{page['title']}: {len(text)} chars")


if __name__ == "__main__":
    main()
