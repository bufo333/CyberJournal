from __future__ import annotations

"""Manual DB migration helper."""

import asyncio

import aiosqlite

from cyberjournal.db import DB_PATH


async def _column_exists(db: aiosqlite.Connection, table: str, column: str) -> bool:
    """Return True if `column` is present in `table`."""
    cur = await db.execute(f"PRAGMA table_info({table})")
    rows = await cur.fetchall()
    await cur.close()
    for row in rows:
        if len(row) >= 2 and (row[1] == column or (hasattr(row, "keys") and row["name"] == column)):
            return True
    return False


async def migrate() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys = ON;")
        statements = []
        if not await _column_exists(db, "entries", "map_nonce"):
            statements.append("ALTER TABLE entries ADD COLUMN map_nonce BLOB;")
        if not await _column_exists(db, "entries", "map_ct"):
            statements.append("ALTER TABLE entries ADD COLUMN map_ct BLOB;")
        if not await _column_exists(db, "entries", "map_format"):
            statements.append("ALTER TABLE entries ADD COLUMN map_format TEXT DEFAULT 'ascii';")
        if not await _column_exists(db, "users", "security_question"):
            statements.append("ALTER TABLE users ADD COLUMN security_question TEXT NOT NULL DEFAULT '';")
        if not await _column_exists(db, "users", "security_answer_hash"):
            statements.append("ALTER TABLE users ADD COLUMN security_answer_hash TEXT NOT NULL DEFAULT '';")

        for stmt in statements:
            await db.execute(stmt)

        if statements:
            await db.commit()


if __name__ == "__main__":
    asyncio.run(migrate())
