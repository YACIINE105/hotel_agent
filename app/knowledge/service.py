"""Per-property knowledge: approved structured facts plus retrievable document chunks."""

import math
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import Unavailable
from app.models import HotelFact, KnowledgeChunk
from app.providers import Providers

WORD = re.compile(r"\w{2,}", re.UNICODE)
MAX_FACT_CHARS = 8000


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
    def __init__(self, session: AsyncSession, providers: Providers | None, property_id: int):
        self.session, self.providers, self.property_id = session, providers, property_id

    async def facts(self, language: str | None) -> list[dict]:
        """Approved facts in the guest's language, falling back to English per key."""
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
        return out

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
        await self.session.flush()
        return row

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
        await self.session.flush()
        return len(chunks)

    async def search(self, query: str, k: int = 4) -> list[dict]:
        rows = (
            await self.session.scalars(select(KnowledgeChunk).where(KnowledgeChunk.property_id == self.property_id))
        ).all()
        if not rows:
            return []
        scored: list[tuple[float, KnowledgeChunk]] = []
        vector = None
        if self.providers and self.providers.s.embedding_model and any(r.embedding for r in rows):
            try:
                vector = (await self.providers.embed([query]))[0]
            except Unavailable:
                vector = None  # degrade to keyword search rather than failing the turn
        q_terms = _terms(query)
        for row in rows:
            if vector and row.embedding and row.embedding_model == self.providers.s.embedding_model:
                dot = sum(a * b for a, b in zip(vector, row.embedding))
                norm = math.sqrt(sum(a * a for a in vector)) * math.sqrt(sum(b * b for b in row.embedding))
                score = dot / norm if norm else 0.0
            else:
                terms = _terms(row.content)
                score = len(q_terms & terms) / (len(q_terms) or 1)
            if score > 0.1:
                scored.append((score, row))
        scored.sort(key=lambda x: (-x[0], x[1].id))
        return [{"id": f"C:{r.id}", "source": r.source, "content": r.content} for _, r in scored[:k]]
