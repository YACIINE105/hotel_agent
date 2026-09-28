# Hotel Agent

A multilingual AI front desk for hotels. It answers guests from approved hotel information, searches live availability, shows exact terms, and books after the guest presses **Confirm**. It also talks by voice with sequenced streaming and hands the conversation to staff at any point. The core is headless: the embeddable widget, the staff inbox, and any external system all use the same API.

Product spec: `../CustomerSupport/HOTEL_AI_AGENT_PRODUCT_PLAN.md`. Build plan: `../CustomerSupport/HOTEL_AGENT_BUILD_PLAN.md`.

## Run it

```bash
cp .env.example .env              # set LLM_API_KEY, LLM_MODEL, SESSION_SECRET
uv sync
./scripts/run_asr.sh              # terminal 1: Qwen3-ASR on vLLM (~/vllm-env), port 8001
./scripts/run_tts.sh              # terminal 2: Kokoro TTS (~/kokoro-env), port 8002
./scripts/run_api.sh              # terminal 3: API + demo, port 8000
```

- Guest demo: http://localhost:8000/ (append `?hotel=oran-medina` for the second hotel). The 🎙 button enters voice mode.
- Staff inbox: http://localhost:8000/inbox. Demo keys: `demo-atlas-staff-key` and `demo-oran-staff-key`.
- API docs: http://localhost:8000/docs
- Tests: `uv run pytest`. Live check against the running servers: `uv run python scripts/smoke_live.py --voice`
- Model latency benchmark: `uv run python scripts/bench_models.py moonshotai/kimi-k3:off deepseek/deepseek-v4.1-flash`

Voice needs `localhost` or HTTPS, because browsers only allow microphone access there. Dev uses SQLite. For Postgres, set `DATABASE_URL=postgresql+asyncpg://...`, `ENVIRONMENT=production`, and run `uv run alembic upgrade head`.

Embed on any hotel site:

```html
<script src="https://YOUR_HOST/static/widget.js" data-hotel="atlas-bay" defer></script>
```

## How it works

```text
widget / voice WS / any client
        │ guest token (HMAC, bound to property + conversation)
        ▼
TurnRunner ── load history, approved facts, relevant docs (property-scoped)
   │  one streamed model call; tools generated from connector capabilities
   ├─ search_availability ─┐
   ├─ prepare_booking ─────┼─► BookingService ─► ReservationConnector (fake → Apaleo/Mews/SiteMinder MCP)
   ├─ find_reservation ────┘      quote → confirm → submit → reconcile, effect ledger
   └─ handoff_to_staff ──► pauses AI; staff inbox takes over
POST /bookings/confirm  ◄── the only booking path: an explicit guest click bound to a quote
```

| Concern | Where |
|---|---|
| Booking contracts, state machine | `app/booking/contracts.py` |
| Idempotent booking, price-change reconfirmation, timeout reconciliation | `app/booking/service.py` |
| Fake reservation system with scriptable faults | `app/booking/connectors/fake.py` |
| Adding a real PMS | implement `app/booking/connectors/base.py`, register in `app/booking/registry.py` |
| Agent turn, prompt, tool loop, audit | `app/agent/orchestrator.py`, `app/agent/tools.py` |
| Streaming text → citation-free sentences | `app/agent/sentences.py` |
| Voice: ASR → agent → in-order TTS, barge-in | `app/api/voice.py`, `app/voice/pipeline.py`, `app/web/widget.js` |

### Voice: why it feels immediate

1. The browser runs its own voice activity detection. It keeps a 0.75 s pre-roll so the first syllable isn't lost, ends the utterance after 650 ms of silence, and sends a 16 kHz WAV.
2. vLLM transcribes it. The model's reply streams, and the first segment is cut at a clause boundary (after at least 40 characters) so speech can start early.
3. Each sentence goes to TTS as soon as it's complete. Up to `TTS_PREFETCH` segments are synthesized while earlier ones play. `SequencedSpeaker` releases audio strictly in order, and the browser schedules it gaplessly.
4. While a tool runs, a localized status line ("Checking live availability…") is spoken, so there is no dead air.
5. Barge-in: if the guest speaks over the agent, playback stops, the server cancels the turn, and the new utterance is processed.

Measured locally with an RTX 3060 laptop GPU, Kokoro on CPU, and kimi-k3 via OpenRouter with `LLM_REASONING=off`:

| End of speech → | API client (`smoke_live.py`) | Real browser, fake mic (`smoke_voice_browser.py`) |
|---|---|---|
| Transcript | ~1.3 s | ~1.0 s |
| First sentence | ~2.6 s | ~3.2 s |
| **First audio** | **~2.7 s** | **~4.0 s** |

Run-to-run variation is dominated by the model's first token through OpenRouter (1–3 s). Most of the remaining delay is the model's first token. Setting `LLM_REASONING=off` halved it for kimi-k3 (2.3 s → 1.0 s in `bench_models.py`). `deepseek/deepseek-v4.1-flash` measured 0.8 s with correct tool calls.

### Safety properties covered by tests

- Hotel A's facts, documents, conversations, and bookings are never visible to hotel B's guests or staff.
- The model cannot book. `confirm_booking` is not a tool; a quote can only come from offers found in this conversation.
- A double click creates one booking. A price change requires reconfirmation. A timeout after the supplier succeeded is reconciled by lookup, not by booking twice. An unreachable supplier leads to staff review plus a handoff.
- Tool calls are replayed faithfully in history. Without this, the live model started claiming "your summary is ready" without calling the tool.
- Money is a Decimal and cards are rendered from structured data. Citations are stripped from the stream and audited per turn.

## Known limits / next steps

- **TTS is English-only** (Kokoro-7M). Arabic and French replies are shown as text in voice mode. Add voices per language to `TTS_LANGUAGES` once you pick an Arabic/French TTS.
- Staff auth is an org API key; replace it with OIDC for the dashboard (plan M6).
- Real connectors: Apaleo sandbox first (plan M5), then Mews/Cloudbeds or SiteMinder MCP. Also build our own MCP server and Zendesk/Intercom handoff connectors (plan M7).
- Channels: WhatsApp, email, and OTA guest messages through the PMS/channel manager (plan M8).
- Postgres row-level security policies. Isolation is currently enforced in every query and covered by tests.
- Language detection is heuristic (EN/AR/FR). The guest can always pick a language in the widget.
