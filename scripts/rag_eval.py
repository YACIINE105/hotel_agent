"""Retrieval evaluation on the real area-guide corpus (data/area_guide) with data/rag_eval.json.

    uv run python scripts/rag_eval.py [--scale 10000]

For each question, a hit means a retrieved passage contains the expected exact phrase.
Reports recall@1 / recall@4 / MRR for the old word-overlap search vs BM25, per language, then
search latency at larger corpus sizes (synthetic copies of the real passages), with the cache.
"""

import argparse
import asyncio
import json
import pathlib
import statistics
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.db import Base, make_engine, make_sessionmaker  # noqa: E402
from app.knowledge.bm25 import BM25Index  # noqa: E402
from app.knowledge.service import KnowledgeService  # noqa: E402
from app.models import KnowledgeChunk, Property  # noqa: E402
from app.seed import SLUG, seed  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]


async def main(scale: int):
    cases = json.loads((ROOT / "data" / "rag_eval.json").read_text())
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = make_sessionmaker(engine)
    async with sm() as s:
        await seed(s)
        prop = await s.scalar(select(Property).where(Property.slug == SLUG))
        chunks = (await s.scalars(select(KnowledgeChunk))).all()
        print(f"corpus: {len(chunks)} passages from {len({c.source for c in chunks})} documents\n")
        for c in cases:  # every expected phrase must really exist in the corpus
            assert any(c["expect"] in ch.content for ch in chunks), f"not in corpus: {c['expect']}"

        results = {}
        for name in ("word overlap (before)", "BM25 (now)"):
            ks = KnowledgeService(s, None, prop.id, prop.knowledge_version)
            fn = ks.legacy_search if name.startswith("word") else ks.search
            rows = []
            for c in cases:
                t0 = time.perf_counter()
                found = await fn(c["q"], k=4)
                ms = (time.perf_counter() - t0) * 1000
                rank = next((i + 1 for i, d in enumerate(found) if c["expect"] in d["content"]), None)
                rows.append({**c, "rank": rank, "ms": ms})
            results[name] = rows

    print(f"{'method':24} {'lang':4} {'n':>3} {'recall@1':>9} {'recall@4':>9} {'MRR':>6} {'median ms':>10}")
    for name, rows in results.items():
        for lang in ("en", "ar", "all"):
            sub = [r for r in rows if lang == "all" or r["lang"] == lang]
            r1 = sum(r["rank"] == 1 for r in sub) / len(sub)
            r4 = sum(r["rank"] is not None for r in sub) / len(sub)
            mrr = sum(1 / r["rank"] for r in sub if r["rank"]) / len(sub)
            print(f"{name:24} {lang:4} {len(sub):>3} {r1:>9.0%} {r4:>9.0%} {mrr:>6.2f} "
                  f"{statistics.median(r['ms'] for r in sub):>10.2f}")
    misses = [r for r in results["BM25 (now)"] if r["rank"] is None]
    if misses:
        print("\nBM25 misses:", *[f"  [{m['lang']}] {m['q']}" for m in misses], sep="\n")

    # Scale: build indexes over N passages (copies of real ones) and time queries.
    print(f"\n{'passages':>9} {'index build':>12} {'query p50':>10} {'query p95':>10}  (BM25, cached index)")
    base = [{"id": f"C:{c.id}", "source": c.source, "content": c.content} for c in chunks]
    for n in sorted({len(base), 2000, scale}):
        docs = [{**base[i % len(base)], "id": f"C:{i}"} for i in range(n)]
        t0 = time.perf_counter()
        index = BM25Index(docs)
        build = time.perf_counter() - t0
        times = []
        for c in cases:
            t0 = time.perf_counter()
            index.search(c["q"], k=4)
            times.append((time.perf_counter() - t0) * 1000)
        times.sort()
        print(f"{n:>9} {build:>11.2f}s {times[len(times) // 2]:>8.2f}ms {times[int(len(times) * 0.95)]:>8.2f}ms")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", type=int, default=10000)
    asyncio.run(main(ap.parse_args().scale))
