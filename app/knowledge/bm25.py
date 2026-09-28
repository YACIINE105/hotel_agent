"""BM25 keyword retrieval with Arabic-aware normalization (no external services).

Normalization: case folding; Arabic diacritics and tatweel removed; alef/yaa/taa-marbuta variants
unified; common proclitics (و، ب، ل، ف، ك + ال) stripped. This lets "والمطار", "المطار" and
"مطار" match, which plain word overlap misses.
"""

import math
import re
from collections import Counter

WORD = re.compile(r"\w+", re.UNICODE)
ARABIC_DIACRITICS = re.compile(r"[ً-ْٰـ]")
ARABIC_PREFIX = re.compile(r"^(?:[وفبكل]?ال|لل|[وف](?=\w{3}))")

STOPWORDS = set("""
a an and are as at be by for from has have how i in is it its of on or that the this to was were what when where
which who will with do does can you your we our there their me my about
هل ما ماذا متى أين اين كيف كم من في على الى إلى عن مع هو هي هذا هذه ذلك التي الذي ان أن او أو لا نعم يوجد عند
""".split())


def normalize(token: str) -> str:
    t = ARABIC_DIACRITICS.sub("", token.casefold())
    t = t.translate(str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه", "ؤ": "و", "ئ": "ي"}))
    if len(t) > 4:
        t = ARABIC_PREFIX.sub("", t, count=1)
    return t


# Stopwords go through the same normalization as the text ("متى" -> "متي"), or they never match.
STOPWORDS = {normalize(w) for w in STOPWORDS}


def tokenize(text: str) -> list[str]:
    out = []
    for w in WORD.findall(text):
        t = normalize(w)
        if len(t) >= 2 and t not in STOPWORDS:
            out.append(t)
    return out


class BM25Index:
    def __init__(self, docs: list[dict], k1: float = 1.2, b: float = 0.75):
        """docs: [{"id": ..., "source": ..., "content": ...}]"""
        self.docs, self.k1, self.b = docs, k1, b
        self.tfs = [Counter(tokenize(d["content"] + " " + d.get("source", ""))) for d in docs]
        self.lengths = [sum(tf.values()) for tf in self.tfs]
        self.avgdl = (sum(self.lengths) / len(self.lengths)) if docs else 0
        df = Counter(term for tf in self.tfs for term in tf)
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        # Inverted index: only documents containing a query term are scored.
        self.postings: dict[str, list[int]] = {}
        for i, tf in enumerate(self.tfs):
            for t in tf:
                self.postings.setdefault(t, []).append(i)

    def search(self, query: str, k: int = 4, min_score: float = 0.2, relative: float = 0.4,
               min_coverage: float = 0.5) -> list[tuple[float, dict]]:
        """min_score is low on purpose: with few passages even a perfect match scores ~1 (small IDF)."""
        terms = set(tokenize(query))
        scores: dict[int, float] = {}
        for t in terms:
            idf = self.idf.get(t)
            if idf is None:
                continue
            for i in self.postings[t]:
                f = self.tfs[i][t]
                denom = f + self.k1 * (1 - self.b + self.b * self.lengths[i] / (self.avgdl or 1))
                scores[i] = scores.get(i, 0.0) + idf * f * (self.k1 + 1) / denom
        # Coverage bonus: prefer passages that contain more of the question's distinct terms
        # (plain BM25 over-rewards short passages repeating one term, e.g. hotel name lists).
        known = [t for t in terms if t in self.idf]
        if known:
            for i in scores:
                covered = sum(1 for t in known if t in self.tfs[i])
                scores[i] *= 0.5 + 0.5 * covered / len(known)
        ranked = sorted(scores.items(), key=lambda x: (-x[1], x[0]))
        if not ranked:
            return []
        # Relevance gates: every irrelevant passage costs model time (a small GPU model pays per token).
        top = ranked[0][1]
        out = []
        for i, sc in ranked:
            covered = sum(1 for t in known if t in self.tfs[i]) / len(known)
            if sc >= min_score and sc >= relative * top and covered >= min_coverage:
                out.append((sc, self.docs[i]))
            if len(out) == k:
                break
        return out
