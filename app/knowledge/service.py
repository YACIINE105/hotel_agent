"""Per-property knowledge: approved structured facts plus retrievable document chunks."""

import math
import re
from collections import OrderedDict

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import Unavailable
from app.knowledge.bm25 import BM25Index
from app.models import HotelFact, KnowledgeChunk, Property
from app.providers import Providers

WORD = re.compile(r"\w{2,}", re.UNICODE)
MAX_FACT_CHARS = 8000
CACHE_ENTRIES = 256  # (property, version, kind) entries per process
_CACHE: OrderedDict = OrderedDict()


def _terms(text: str) -> set[str]:
    return {w.casefold() for w in WORD.findall(text)}


def split_sections(text: str, limit: int = 900) -> list[str]:
    """Paragraph-aware chunks: never cut mid-paragraph unless a paragraph exceeds the limit."""
    chunks, current = [], ""
    for para in (p.strip() for p in re.split(r"\n\s*\n", text)):
        if not para:
            continue
        while len(para) > limit:
            cut = para.rfind(". ", 0, limit)
            cut = cut + 1 if cut > limit // 2 else limit
            chunks.append(para[:cut].strip())
            para = para[cut:].strip()
        if current and len(current) + len(para) + 2 > limit:
            chunks.append(current)
            current = ""
        current = f"{current}\n\n{para}".strip()
    if current:
        chunks.append(current)
    return chunks


class KnowledgeService:
    def __init__(self, session: AsyncSession, providers: Providers | None, property_id: int,
                 version: int | None = None):
        """version: the property's knowledge_version; when given, facts and the search index are
        served from a per-process cache keyed by it (any edit bumps the version)."""
        self.session, self.providers, self.property_id, self.version = session, providers, property_id, version

    # --- cache --------------------------------------------------------------------------------

    def _key(self, name: str) -> tuple:
        # The database URL is part of the key: property ids and versions repeat across databases.
        return (str(self.session.bind.url), self.property_id, self.version, name)

    def _cached(self, name: str):
        if self.version is None:
            return None
        return _CACHE.get(self._key(name))

    def _store(self, name: str, value):
        if self.version is not None:
            _CACHE[self._key(name)] = value
            _CACHE.move_to_end(self._key(name))
            while len(_CACHE) > CACHE_ENTRIES:
                _CACHE.popitem(last=False)
        return value

    async def _bump_version(self) -> None:
        await self.session.execute(update(Property).where(Property.id == self.property_id)
                                   .values(knowledge_version=Property.knowledge_version + 1))

    # --- facts --------------------------------------------------------------------------------

    async def facts(self, language: str | None) -> list[dict]:
        """Approved facts in the guest's language, falling back to English per key."""
        cached = self._cached(f"facts:{language}")
        if cached is not None:
            return cached
        rows = (
            await self.session.scalars(
                select(HotelFact).where(HotelFact.property_id == self.property_id, HotelFact.approved.is_(True))
            )
        ).all()
        by_key: dict[str, HotelFact] = {}
        for row in rows:
            best = by_key.get(row.key)
            if best is None or row.language == language or (best.language != language and row.language == "en"):
                by_key[row.key] = row
        out, size = [], 0
        for key in sorted(by_key):
            size += len(by_key[key].content)
            if size > MAX_FACT_CHARS:
                break
            out.append({"id": f"F:{key}", "content": by_key[key].content, "language": by_key[key].language})
        return self._store(f"facts:{language}", out)

    async def upsert_fact(self, key: str, language: str, content: str, approved: bool = True) -> HotelFact:
        row = await self.session.scalar(
            select(HotelFact).where(
                HotelFact.property_id == self.property_id, HotelFact.key == key, HotelFact.language == language
            )
        )
        if row is None:
            row = HotelFact(property_id=self.property_id, key=key, language=language, content=content,
                            approved=approved)
            self.session.add(row)
        else:
            row.content, row.approved = content, approved
        await self._bump_version()
        await self.session.flush()
        return row

    # --- documents ----------------------------------------------------------------------------

    async def ingest(self, source: str, text: str) -> int:
        chunks = split_sections(text)
        vectors: list[list[float] | None] = [None] * len(chunks)
        model = ""
        if self.providers and self.providers.s.embedding_model:
            vectors = []
            for i in range(0, len(chunks), 16):
                vectors += await self.providers.embed(chunks[i : i + 16])
            model = self.providers.s.embedding_model
        for content, vector in zip(chunks, vectors, strict=True):
            self.session.add(KnowledgeChunk(property_id=self.property_id, source=source, content=content,
                                            embedding=vector, embedding_model=model))
        await self._bump_version()
        await self.session.flush()
        return len(chunks)

    async def _index(self) -> BM25Index:
        cached = self._cached("bm25")
        if cached is not None:
            return cached
        rows = (await self.session.execute(
            select(KnowledgeChunk.id, KnowledgeChunk.source, KnowledgeChunk.content)
            .where(KnowledgeChunk.property_id == self.property_id).order_by(KnowledgeChunk.id))).all()
        docs = [{"id": f"C:{i}", "source": src, "content": content} for i, src, content in rows]
        return self._store("bm25", BM25Index(docs))

    async def search(self, query: str, k: int = 4) -> list[dict]:
        """BM25 over the property's documents; fused with vector similarity when embeddings exist."""
        index = await self._index()
        if not index.docs:
            return []
        keyword = [doc for _, doc in index.search(query, k=max(k, 20))]
        vector_ranked = await self._vector_rank(query, k=20)
        if not vector_ranked:
            return keyword[:k]
        # Reciprocal rank fusion of keyword and vector rankings.
        scores: dict[str, float] = {}
        by_id = {d["id"]: d for d in keyword + vector_ranked}
        for ranking in (keyword, vector_ranked):
            for rank, d in enumerate(ranking):
                scores[d["id"]] = scores.get(d["id"], 0.0) + 1 / (60 + rank)
        return [by_id[i] for i, _ in sorted(scores.items(), key=lambda x: -x[1])[:k]]

    async def _vector_rank(self, query: str, k: int) -> list[dict]:
        if not (self.providers and self.providers.s.embedding_model):
            return []
        rows = (await self.session.scalars(select(KnowledgeChunk).where(
            KnowledgeChunk.property_id == self.property_id,
            KnowledgeChunk.embedding_model == self.providers.s.embedding_model))).all()
        if not rows:
            return []
        try:
            vector = (await self.providers.embed([query]))[0]
        except Unavailable:
            return []  # degrade to keyword search rather than failing the turn
        qn = math.sqrt(sum(a * a for a in vector)) or 1.0
        scored = []
        for r in rows:
            if r.embedding:
                dot = sum(a * b for a, b in zip(vector, r.embedding))
                scored.append((dot / (qn * (math.sqrt(sum(b * b for b in r.embedding)) or 1.0)), r))
        scored.sort(key=lambda x: (-x[0], x[1].id))
        return [{"id": f"C:{r.id}", "source": r.source, "content": r.content} for _, r in scored[:k]]

    async def legacy_search(self, query: str, k: int = 4) -> list[dict]:
        """The previous word-overlap ranking, kept only to compare in scripts/rag_eval.py."""
        rows = (await self.session.scalars(
            select(KnowledgeChunk).where(KnowledgeChunk.property_id == self.property_id))).all()
        q_terms = _terms(query)
        scored = [(len(q_terms & _terms(r.content)) / (len(q_terms) or 1), r) for r in rows]
        scored = [x for x in scored if x[0] > 0.1]
        scored.sort(key=lambda x: (-x[0], x[1].id))
        return [{"id": f"C:{r.id}", "source": r.source, "content": r.content} for _, r in scored[:k]]
