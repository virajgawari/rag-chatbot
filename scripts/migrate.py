"""Apply versioned SQL migrations to the configured Supabase Postgres database.

Usage:
    python scripts/migrate.py            # apply pending migrations
    python scripts/migrate.py --status   # list applied / pending versions

Each file in migrations/ is applied once inside a transaction and recorded in
schema_migrations with a SHA-256 checksum. Changing an applied file is reported
as a warning instead of being silently re-run.
"""
import argparse
import asyncio
import hashlib
import sys
from pathlib import Path

from asyncpg import connect

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.core.config import settings  # noqa: E402

MIGRATIONS_DIR = PROJECT_ROOT / "migrations"


def _checksum(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


async def _run(status_only: bool) -> int:
    if not settings.DATABASE_URL:
        print("ERROR: DATABASE_URL is not set. Add the Postgres connection string to .env.")
        return 2

    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        print("ERROR: no migrations found in migrations/.")
        return 2

    connection = await connect(dsn=settings.DATABASE_URL, timeout=30.0, command_timeout=60.0)
    try:
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                checksum TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
            );
            """
        )
        applied = {
            row["version"]: row["checksum"]
            for row in await connection.fetch("SELECT version, checksum FROM schema_migrations;")
        }

        pending = []
        for path in files:
            version = path.stem
            sql = path.read_text(encoding="utf-8")
            digest = _checksum(sql)
            if version in applied:
                if applied[version] != digest:
                    print(f"WARNING: {version} changed after being applied; not re-running.")
                continue
            pending.append((version, sql, digest))

        if status_only:
            for path in files:
                state = "applied" if path.stem in applied else "pending"
                print(f"  [{state:7}] {path.stem}")
            return 0

        if not pending:
            print("Database schema is up to date; no pending migrations.")
            return 0

        for version, sql, digest in pending:
            print(f"Applying {version} ...", end=" ", flush=True)
            async with connection.transaction():
                await connection.execute(sql)
                await connection.execute(
                    "INSERT INTO schema_migrations (version, checksum) VALUES ($1, $2);",
                    version,
                    digest,
                )
            print("ok")

        print(f"Applied {len(pending)} migration(s).")
        return 0
    finally:
        await connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply DocChat database migrations")
    parser.add_argument("--status", action="store_true", help="show applied and pending versions")
    args = parser.parse_args()
    return asyncio.run(_run(args.status))


if __name__ == "__main__":
    raise SystemExit(main())
