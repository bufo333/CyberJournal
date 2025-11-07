# =====================
# File: cyberjournal/db.py
# =====================
"""SQLite schema and data-access layer.

This module contains only SQL + simple mappers. All crypto/business logic is
implemented in :mod:`cyberjournal.logic`.
"""
from __future__ import annotations

from typing import Optional
import os
import aiosqlite

DB_PATH = os.environ.get("CYBERJOURNAL_DB", "journal_encrypted.sqlite3")

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT UNIQUE NOT NULL,
    pwd_hash        TEXT NOT NULL,
    kek_salt        BLOB NOT NULL,
    dek_wrapped     BLOB NOT NULL,
    dek_wrap_nonce  BLOB NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    created_at      TEXT NOT NULL,
    title_nonce     BLOB NOT NULL,
    title_ct        BLOB NOT NULL,
    body_nonce      BLOB NOT NULL,
    body_ct         BLOB NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS entry_terms (
    entry_id        INTEGER NOT NULL,
    term_hash       BLOB NOT NULL,
    UNIQUE(entry_id, term_hash),
    FOREIGN KEY (entry_id) REFERENCES entries(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_terms_hash ON entry_terms(term_hash);
CREATE INDEX IF NOT EXISTS idx_entries_user ON entries(user_id);
"""


async def init_db(db_path: str = DB_PATH) -> None:
    """Initialize database schema if not present."""
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(SCHEMA_SQL)
        await db.commit()


# --- User table ops ---
async def user_by_username(username: str) -> Optional[aiosqlite.Row]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE username = ?", (username,))
        row = await cur.fetchone()
        await cur.close()
        return row


async def insert_user(username: str, pwd_hash: str, kek_salt: bytes, dek_wrapped: bytes, dek_wrap_nonce: bytes, created_at: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO users (username, pwd_hash, kek_salt, dek_wrapped, dek_wrap_nonce, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (username, pwd_hash, kek_salt, dek_wrapped, dek_wrap_nonce, created_at),
        )
        await db.commit()


# --- Entry table ops ---
async def insert_entry(user_id: int, created_at: str, title_nonce: bytes, title_ct: bytes, body_nonce: bytes, body_ct: bytes) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO entries (user_id, created_at, title_nonce, title_ct, body_nonce, body_ct) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, created_at, title_nonce, title_ct, body_nonce, body_ct),
        )
        entry_id = cur.lastrowid
        await db.commit()
        return int(entry_id)


async def fetch_entry(user_id: int, entry_id: int) -> Optional[aiosqlite.Row]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT created_at, title_nonce, title_ct, body_nonce, body_ct FROM entries WHERE id = ? AND user_id = ?",
            (entry_id, user_id),
        )
        row = await cur.fetchone()
        await cur.close()
        return row


async def fetch_entries_for_user(user_id: int) -> list[aiosqlite.Row]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id, created_at, title_nonce, title_ct FROM entries WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        )
        rows = await cur.fetchall()
        await cur.close()
        return list(rows)


# --- Blind index ops ---
async def insert_terms(entry_id: int, term_hashes: list[bytes]) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany(
            "INSERT OR IGNORE INTO entry_terms (entry_id, term_hash) VALUES (?, ?)",
            [(entry_id, th) for th in term_hashes],
        )
        await db.commit()


async def fetch_entry_ids_for_term_hash(term_hash: bytes) -> set[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT entry_id FROM entry_terms WHERE term_hash = ?", (term_hash,))
        rows = await cur.fetchall()
        await cur.close()
        return {int(r[0]) for r in rows}


