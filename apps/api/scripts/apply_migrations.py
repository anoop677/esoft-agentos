# ruff: noqa: I001

import asyncio
import os
from pathlib import Path

import asyncpg


MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


async def apply_migrations() -> None:
    database_url = os.environ.get("MIGRATION_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError(
            "MIGRATION_DATABASE_URL or DATABASE_URL must be set to apply database migrations"
        )

    connection = await asyncpg.connect(database_url)
    try:
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS public.schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )

        for migration_path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            async with connection.transaction():
                already_applied = await connection.fetchval(
                    "SELECT 1 FROM public.schema_migrations WHERE version = $1",
                    migration_path.name,
                )
                if already_applied:
                    continue

                await connection.execute(migration_path.read_text(encoding="utf-8"))
                await connection.execute(
                    "INSERT INTO public.schema_migrations (version) VALUES ($1)",
                    migration_path.name,
                )
                print(f"Applied migration: {migration_path.name}")

        print("Database migrations are up to date.")
    finally:
        await connection.close()


if __name__ == "__main__":
    asyncio.run(apply_migrations())
