import asyncpg
import os
from pathlib import Path

DATABASE_URL = os.getenv("DATABASE_URL", "postgres://app:trellisAI@postgres:5432/appdb")

class DB:
    def __init__(self, dsn: str = DATABASE_URL):
        self.dsn = dsn

    async def connect(self):
        return await asyncpg.connect(self.dsn)

async def run_migrations():
    """Apply the base migration SQL file."""
    mig = Path(__file__).resolve().parents[2] / "migrations" / "001_create_tables.sql"
    sql = mig.read_text()

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute(sql)
        print("Migrations applied successfully")
    finally:
        await conn.close()
