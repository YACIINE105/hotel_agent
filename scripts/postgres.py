"""Local Postgres 16 (+pgvector) without system packages, via the `pgserver` wheel.

    uv run python scripts/postgres.py            # start (or reuse) and print the DATABASE_URL
    uv run python scripts/postgres.py stop

Data lives in .run/pgdata; the server keeps running after this script exits.
Use the printed URL as DATABASE_URL (app) or TEST_POSTGRES_URL (tests).
"""

import pathlib
import sys

import pgserver

DATA = pathlib.Path(__file__).resolve().parents[1] / ".run" / "pgdata"


def main():
    DATA.parent.mkdir(exist_ok=True)
    server = pgserver.get_server(str(DATA), cleanup_mode=None)
    if sys.argv[1:] == ["stop"]:
        server.cleanup()
        print("stopped")
        return
    exists = server.psql("SELECT 1 FROM pg_database WHERE datname = 'hotel_agent';")
    if "1 row" not in exists:
        server.psql("CREATE DATABASE hotel_agent;")
    print(f"postgresql+asyncpg://postgres@/hotel_agent?host={DATA}")


if __name__ == "__main__":
    main()
