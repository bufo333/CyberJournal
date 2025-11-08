# -*- coding: utf-8 -*-
"""SQLite schema and async data access for CyberJournal."""
from __future__ import annotations

from typing import List, Sequence, Tuple
import os

import aiosqlite

DB_PATH = os.environ.get("CYBERJOURNAL_DB", "journal_encrypted.sqlite3")


# ---------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------

SCHEMA_SQL = """    PRAGMA journal_mode=WAL;

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


# ---------------------------------------------------------------------
# Connection / initialization
# ---------------------------------------------------------------------

async def init_db() -> None:
    """Create tables if they don't exist."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA_SQL)
        await db.commit()


# ---------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------

async def get_user_by_username(username: str):
    """Fetch a user row by *username*; returns Row or None."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE username = ?", (username,))
        row = await cur.fetchone()
        await cur.close()
        return row

async def insert_user(
    username: str,
    pwd_hash: str,
    kek_salt: bytes,
    dek_wrapped: bytes,
    dek_wrap_nonce: bytes,
    created_at: str,
) -> None:
    """Insert a newly registered user."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """                INSERT INTO users (username, pwd_hash, kek_salt, dek_wrapped, dek_wrap_nonce, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """.strip(),
            (username, pwd_hash, kek_salt, dek_wrapped, dek_wrap_nonce, created_at),
        )
        await db.commit()


# ---------------------------------------------------------------------
# Entries and search terms
# ---------------------------------------------------------------------

async def insert_entry_row(
    user_id: int,
    created_at: str,
    title_nonce: bytes,
    title_ct: bytes,
    body_nonce: bytes,
    body_ct: bytes,
) -> int:
    """Insert an encrypted entry; return new entry id."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """                INSERT INTO entries (user_id, created_at, title_nonce, title_ct, body_nonce, body_ct)
            VALUES (?, ?, ?, ?, ?, ?)
            """.strip(),
            (user_id, created_at, title_nonce, title_ct, body_nonce, body_ct),
        )
        eid = cur.lastrowid
        await db.commit()
        return int(eid)

async def insert_entry_terms(pairs: Sequence[Tuple[int, bytes]]) -> None:
    """Bulk insert blind-index term rows."""
    if not pairs:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany(
            "INSERT OR IGNORE INTO entry_terms (entry_id, term_hash) VALUES (?, ?)",
            list(pairs),
        )
        await db.commit()

async def list_entry_headers(user_id: int):
    """Return rows of (id, created_at, title_nonce, title_ct) for a user."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """                SELECT id, created_at, title_nonce, title_ct
            FROM entries
            WHERE user_id = ?
            ORDER BY created_at DESC
            """.strip(),
            (user_id,),
        )
        rows = await cur.fetchall()
        await cur.close()
        return rows

async def get_entry_row(user_id: int, entry_id: int):
    """Return a single entry row or None for this user."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """                SELECT created_at, title_nonce, title_ct, body_nonce, body_ct
            FROM entries
            WHERE id = ? AND user_id = ?
            """.strip(),
            (entry_id, user_id),
        )
        row = await cur.fetchone()
        await cur.close()
        return row

async def get_entry_ids_for_term(term_hash: bytes) -> List[int]:
    """Return a list of entry ids that contain the given term hash."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT entry_id FROM entry_terms WHERE term_hash = ?",
            (term_hash,),
        )
        rows = await cur.fetchall()
        await cur.close()
        return [int(r[0]) for r in rows]

import aiosqlite

async def update_entry_row(entry_id: int, user_id: int,
                           title_nonce: bytes, title_ct: bytes,
                           body_nonce: bytes, body_ct: bytes) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE entries
               SET title_nonce = ?, title_ct = ?, body_nonce = ?, body_ct = ?
             WHERE id = ? AND user_id = ?
            """,
            (title_nonce, title_ct, body_nonce, body_ct, entry_id, user_id),
        )
        await db.commit()


async def delete_entry_row(entry_id: int, user_id: int) -> None:
    # entry_terms uses FK ON DELETE CASCADE, so removing entries row clears terms
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM entries WHERE id = ? AND user_id = ?",
            (entry_id, user_id),
        )
        await db.commit()


async def clear_entry_terms(entry_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM entry_terms WHERE entry_id = ?", (entry_id,))
        await db.commit()
