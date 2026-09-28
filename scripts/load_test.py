"""Concurrent-guest load test.

    uv run python scripts/load_test.py chat --base http://localhost:8000 --concurrency 1 4 8 16 --turns 3
    uv run python scripts/load_test.py poll --base http://localhost:8000 --widgets 100 500 --seconds 20

chat: each simulated guest opens a session and sends --turns FAQ questions one after another.
      Reports time to first text, total reply time (p50/p95), throughput, and errors.
poll: simulated open widgets each poll /messages every 2 s (the fallback); reports latency.
live: N open push streams (what the widget does now); staff replies to a sample, delivery latency.
"""

import argparse
import asyncio
import json
import statistics
import time

import httpx

HOTEL = "steigenberger-aldau"
QUESTIONS = ["What time is check-in?", "Is parking free?", "Do you have a spa?", "Are pets allowed?",
             "Where is the hotel?", "Is there a kids club?", "Is Wi-Fi free?", "Do you have a golf course?"]


def pct(values, p):
    if not values:
        return float("nan")
    values = sorted(values)
    return values[min(len(values) - 1, int(round(p / 100 * (len(values) - 1))))]


async def guest(client, i, turns, stats):
    try:
        s = (await client.post(f"/v1/properties/{HOTEL}/sessions", json={"language": "en"})).json()
    except Exception as exc:  # noqa: BLE001
        stats["errors"].append(f"session: {exc!r}")
        return
    headers = {"Authorization": f"Bearer {s['token']}"}
    for t in range(turns):
        q = QUESTIONS[(i + t) % len(QUESTIONS)]
        t0 = time.perf_counter()
        first = None
        failed = None
        try:
            async with client.stream("POST", f"/v1/conversations/{s['conversation_id']}/messages",
                                     json={"text": q}, headers=headers) as r:
                if r.status_code != 200:
                    failed = f"HTTP {r.status_code}"
                else:
                    async for line in r.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        ev = json.loads(line[6:])
                        if ev["type"] == "delta" and first is None:
                            first = time.perf_counter() - t0
                        if ev["type"] == "error":
                            failed = "agent error event (model unavailable?)"
        except Exception as exc:  # noqa: BLE001
            failed = repr(exc)[:120]
        total = time.perf_counter() - t0
        if failed:
            stats["errors"].append(failed)
        else:
            stats["ttft"].append(first or total)
            stats["total"].append(total)


async def run_chat(base, levels, turns):
    limits = httpx.Limits(max_connections=1000, max_keepalive_connections=1000)
    async with httpx.AsyncClient(base_url=base, trust_env=False, timeout=180, limits=limits) as client:
        print(f"{'guests':>6} {'turns':>6} {'first p50':>10} {'first p95':>10} {'reply p50':>10} {'reply p95':>10} "
              f"{'replies/s':>9} {'errors':>6}")
        for n in levels:
            stats = {"ttft": [], "total": [], "errors": []}
            t0 = time.perf_counter()
            await asyncio.gather(*(guest(client, i, turns, stats) for i in range(n)))
            wall = time.perf_counter() - t0
            ok = len(stats["total"])
            print(f"{n:>6} {ok + len(stats['errors']):>6} {pct(stats['ttft'], 50):>9.2f}s {pct(stats['ttft'], 95):>9.2f}s "
                  f"{pct(stats['total'], 50):>9.2f}s {pct(stats['total'], 95):>9.2f}s {ok / wall:>9.1f} {len(stats['errors']):>6}")
            if stats["errors"]:
                print("        sample errors:", sorted(set(stats["errors"]))[:3])


async def run_poll(base, levels, seconds):
    limits = httpx.Limits(max_connections=2000, max_keepalive_connections=2000)
    async with httpx.AsyncClient(base_url=base, trust_env=False, timeout=30, limits=limits) as client:
        print(f"{'widgets':>7} {'req/s':>7} {'p50':>8} {'p95':>8} {'p99':>8} {'errors':>6}")
        for n in levels:
            sessions = []
            for chunk in range(0, n, 50):
                batch = await asyncio.gather(*(client.post(f"/v1/properties/{HOTEL}/sessions", json={})
                                               for _ in range(min(50, n - chunk))))
                sessions += [r.json() for r in batch]
            lat, errors = [], 0
            stop = time.perf_counter() + seconds

            async def widget(s, offset):
                nonlocal errors
                await asyncio.sleep(offset)  # spread polls like real widgets opened at different times
                h = {"Authorization": f"Bearer {s['token']}"}
                while time.perf_counter() < stop:
                    t0 = time.perf_counter()
                    try:
                        r = await client.get(f"/v1/conversations/{s['conversation_id']}/messages?after=0", headers=h)
                        if r.status_code != 200:
                            errors += 1
                    except Exception:  # noqa: BLE001
                        errors += 1
                    lat.append(time.perf_counter() - t0)
                    await asyncio.sleep(max(0, 2 - (time.perf_counter() - t0)))

            t0 = time.perf_counter()
            await asyncio.gather(*(widget(s, 2 * i / n) for i, s in enumerate(sessions)))
            wall = time.perf_counter() - t0
            print(f"{n:>7} {len(lat) / wall:>7.0f} {pct(lat, 50) * 1000:>6.0f}ms {pct(lat, 95) * 1000:>6.0f}ms "
                  f"{pct(lat, 99) * 1000:>6.0f}ms {errors:>6}")


async def run_live(base, levels, staff_key, samples=20):
    """Open N push streams (idle widgets), then send staff replies to a sample and time delivery."""
    limits = httpx.Limits(max_connections=5000, max_keepalive_connections=5000)
    async with httpx.AsyncClient(base_url=base, trust_env=False, timeout=None, limits=limits) as client:
        print(f"{'streams':>7} {'open ok':>7} {'deliver p50':>12} {'deliver p95':>12} {'delivered':>9}")
        for n in levels:
            sessions = []
            for chunk in range(0, n, 100):
                batch = await asyncio.gather(*(client.post(f"/v1/properties/{HOTEL}/sessions", json={})
                                               for _ in range(min(100, n - chunk))))
                sessions += [r.json() for r in batch]
            got: dict[str, float] = {}
            opened = 0

            async def listen(s):
                nonlocal opened
                url = f"/v1/conversations/{s['conversation_id']}/events?token={s['token']}"
                try:
                    async with client.stream("GET", url) as r:
                        opened += r.status_code == 200
                        async for line in r.aiter_lines():
                            if line == "event: message":
                                got[s["conversation_id"]] = time.perf_counter()
                                return
                except Exception:  # noqa: BLE001
                    return

            tasks = [asyncio.create_task(listen(s)) for s in sessions]
            await asyncio.sleep(3 + n / 150)  # let every stream connect and finish its first DB read
            sample = sessions[:: max(1, n // samples)][:samples]
            sent = {}
            for s in sample:
                sent[s["conversation_id"]] = time.perf_counter()
                await client.post(f"/v1/staff/conversations/{s['conversation_id']}/reply",
                                  json={"text": "Hello from reception"}, headers={"X-API-Key": staff_key})
            await asyncio.sleep(3)
            lat = [got[c] - t for c, t in sent.items() if c in got]
            print(f"{n:>7} {opened:>7} {pct(lat, 50) * 1000:>10.0f}ms {pct(lat, 95) * 1000:>10.0f}ms "
                  f"{len(lat):>4}/{len(sent):<4}")
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["chat", "poll", "live"])
    ap.add_argument("--streams", type=int, nargs="+", default=[500, 1000, 2000])
    ap.add_argument("--staff-key", default="demo-aldau-staff-key")
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--concurrency", type=int, nargs="+", default=[1, 4, 8, 16])
    ap.add_argument("--turns", type=int, default=3)
    ap.add_argument("--widgets", type=int, nargs="+", default=[100, 500])
    ap.add_argument("--seconds", type=int, default=20)
    a = ap.parse_args()
    if a.mode == "chat":
        asyncio.run(run_chat(a.base, a.concurrency, a.turns))
    elif a.mode == "poll":
        asyncio.run(run_poll(a.base, a.widgets, a.seconds))
    else:
        asyncio.run(run_live(a.base, a.streams, a.staff_key))


if __name__ == "__main__":
    main()
