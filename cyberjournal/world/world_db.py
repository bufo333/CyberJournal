# -*- coding: utf-8 -*-
"""World state persistence — separate SQLite DB for world simulation data."""
from __future__ import annotations

import os
from typing import Optional

import aiosqlite

WORLD_DB_PATH = os.environ.get("CYBERJOURNAL_WORLD_DB", "journal_world.sqlite3")

WORLD_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS world_meta (
    key         TEXT PRIMARY KEY,
    value       TEXT
);

CREATE TABLE IF NOT EXISTS world_tiles (
    x           INTEGER NOT NULL,
    y           INTEGER NOT NULL,
    terrain     TEXT NOT NULL,
    elevation   REAL NOT NULL DEFAULT 0.5,
    moisture    REAL NOT NULL DEFAULT 0.5,
    biome       TEXT NOT NULL DEFAULT 'field',
    entry_id    INTEGER,
    chunk_x     INTEGER NOT NULL DEFAULT 0,
    chunk_y     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (x, y)
);

CREATE INDEX IF NOT EXISTS idx_tiles_chunk ON world_tiles(chunk_x, chunk_y);
CREATE INDEX IF NOT EXISTS idx_tiles_entry ON world_tiles(entry_id);

CREATE TABLE IF NOT EXISTS world_entities (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    x           INTEGER NOT NULL,
    y           INTEGER NOT NULL,
    type        TEXT NOT NULL,
    name        TEXT NOT NULL,
    properties  TEXT DEFAULT '{}',
    entry_id    INTEGER,
    created_turn INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_entities_pos ON world_entities(x, y);
CREATE INDEX IF NOT EXISTS idx_entities_entry ON world_entities(entry_id);

CREATE TABLE IF NOT EXISTS world_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    turn        INTEGER NOT NULL,
    event_type  TEXT NOT NULL,
    description TEXT NOT NULL,
    x           INTEGER,
    y           INTEGER,
    entry_id    INTEGER,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_history_turn ON world_history(turn);
"""


async def init_world_db() -> None:
    """Create world tables if they don't exist."""
    async with aiosqlite.connect(WORLD_DB_PATH) as conn:
        await conn.executescript(WORLD_SCHEMA)
        await conn.commit()


async def get_meta(key: str) -> Optional[str]:
    """Get a world metadata value."""
    async with aiosqlite.connect(WORLD_DB_PATH) as conn:
        cur = await conn.execute("SELECT value FROM world_meta WHERE key = ?", (key,))
        row = await cur.fetchone()
        await cur.close()
        return row[0] if row else None


async def set_meta(key: str, value: str) -> None:
    """Set a world metadata value."""
    async with aiosqlite.connect(WORLD_DB_PATH) as conn:
        await conn.execute(
            "INSERT OR REPLACE INTO world_meta (key, value) VALUES (?, ?)",
            (key, value),
        )
        await conn.commit()


async def set_tiles_batch(tiles: list[dict]) -> None:
    """Insert or replace a batch of tiles.

    Each tile dict: {x, y, terrain, elevation, moisture, biome, entry_id, chunk_x, chunk_y}
    """
    if not tiles:
        return
    async with aiosqlite.connect(WORLD_DB_PATH) as conn:
        await conn.executemany(
            """
            INSERT OR REPLACE INTO world_tiles
                (x, y, terrain, elevation, moisture, biome, entry_id, chunk_x, chunk_y)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (t["x"], t["y"], t["terrain"], t["elevation"], t["moisture"],
                 t["biome"], t.get("entry_id"), t["chunk_x"], t["chunk_y"])
                for t in tiles
            ],
        )
        await conn.commit()


async def get_tiles_in_rect(x1: int, y1: int, x2: int, y2: int) -> list:
    """Return tiles in a rectangular region."""
    async with aiosqlite.connect(WORLD_DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT * FROM world_tiles WHERE x >= ? AND x <= ? AND y >= ? AND y <= ?",
            (x1, x2, y1, y2),
        )
        rows = await cur.fetchall()
        await cur.close()
        return rows


async def get_tile(x: int, y: int):
    """Return a single tile or None."""
    async with aiosqlite.connect(WORLD_DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT * FROM world_tiles WHERE x = ? AND y = ?", (x, y)
        )
        row = await cur.fetchone()
        await cur.close()
        return row


async def clear_tiles_for_entry(entry_id: int) -> None:
    """Remove all tiles generated from a specific entry."""
    async with aiosqlite.connect(WORLD_DB_PATH) as conn:
        await conn.execute("DELETE FROM world_tiles WHERE entry_id = ?", (entry_id,))
        await conn.commit()


async def insert_entity(
    x: int, y: int, etype: str, name: str,
    properties: str = "{}", entry_id: int | None = None, turn: int = 0,
) -> int:
    """Insert a world entity. Returns id."""
    async with aiosqlite.connect(WORLD_DB_PATH) as conn:
        cur = await conn.execute(
            """
            INSERT INTO world_entities (x, y, type, name, properties, entry_id, created_turn)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (x, y, etype, name, properties, entry_id, turn),
        )
        await conn.commit()
        return cur.lastrowid


async def get_entities_in_rect(x1: int, y1: int, x2: int, y2: int) -> list:
    """Return entities in a rectangular region."""
    async with aiosqlite.connect(WORLD_DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT * FROM world_entities WHERE x >= ? AND x <= ? AND y >= ? AND y <= ?",
            (x1, x2, y1, y2),
        )
        rows = await cur.fetchall()
        await cur.close()
        return rows


async def get_entities_for_entry(entry_id: int) -> list:
    """Return entities generated from a specific entry."""
    async with aiosqlite.connect(WORLD_DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT * FROM world_entities WHERE entry_id = ?", (entry_id,)
        )
        rows = await cur.fetchall()
        await cur.close()
        return rows


async def clear_entities_for_entry(entry_id: int) -> None:
    """Remove all entities generated from a specific entry."""
    async with aiosqlite.connect(WORLD_DB_PATH) as conn:
        await conn.execute("DELETE FROM world_entities WHERE entry_id = ?", (entry_id,))
        await conn.commit()


async def insert_history_event(
    turn: int, event_type: str, description: str,
    x: int | None = None, y: int | None = None,
    entry_id: int | None = None, created_at: str = "",
) -> int:
    """Record a world history event."""
    async with aiosqlite.connect(WORLD_DB_PATH) as conn:
        cur = await conn.execute(
            """
            INSERT INTO world_history (turn, event_type, description, x, y, entry_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (turn, event_type, description, x, y, entry_id, created_at),
        )
        await conn.commit()
        return cur.lastrowid


async def get_history(limit: int = 100) -> list:
    """Return world history events, most recent first."""
    async with aiosqlite.connect(WORLD_DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT * FROM world_history ORDER BY turn DESC, id DESC LIMIT ?",
            (limit,),
        )
        rows = await cur.fetchall()
        await cur.close()
        return rows


async def get_all_tiles_sampled(step: int = 4) -> list:
    """Return sampled tiles for minimap rendering."""
    async with aiosqlite.connect(WORLD_DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT * FROM world_tiles WHERE x % ? = 0 AND y % ? = 0",
            (step, step),
        )
        rows = await cur.fetchall()
        await cur.close()
        return rows


async def get_current_turn() -> int:
    """Return the current world turn (number of entries processed)."""
    val = await get_meta("current_turn")
    return int(val) if val else 0
