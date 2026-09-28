#!/usr/bin/env bash
# API server. WORKERS>1 needs Postgres (DATABASE_URL=postgresql+asyncpg://...): workers share
# the database, the typing indicator, the simulator inventory, and push notifications (LISTEN/NOTIFY).
set -euo pipefail
cd "$(dirname "$0")/.."
workers="${WORKERS:-1}"
if [[ "$workers" -gt 1 ]] && ! grep -qE '^DATABASE_URL=postgresql' .env 2>/dev/null && [[ "${DATABASE_URL:-}" != postgresql* ]]; then
  echo "WORKERS=$workers needs Postgres; using 1 worker with SQLite" >&2; workers=1
fi
exec uv run uvicorn app.main:app --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}" --workers "$workers" "$@"
