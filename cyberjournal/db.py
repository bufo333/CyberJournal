#! /usr/bin/python3
# -*- coding: utf-8 -*-
"""SQLite schema and async data access for CyberJournal."""
from __future__ import annotations

from typing import List, Sequence, Tuple, Optional
import os
import aiosqlite

DB_PATH = os.environ.get("CYBERJOURNAL_DB", "journal_encrypted.sqlite3")


# ---------------------------------------------------------------------
# Base schema (new installs)
# ---------------------------------------------------------------------

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    username              TEXT UNIQUE NOT NULL,
    pwd_hash              TEXT NOT NULL,
    kek_salt              BLOB NOT NULL,
    dek_wrapped           BLOB NOT NULL,
    dek_wrap_nonce        BLOB NOT NULL,
    security_question     TEXT NOT NULL DEFAULT '',
    security_answer_hash  TEXT NOT NULL DEFAULT '',
    created_at            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    created_at      TEXT NOT NULL,

    -- Encrypted title
    title_nonce     BLOB NOT NULL,
    title_ct        BLOB NOT NULL,

    -- Encrypted body
    body_nonce      BLOB NOT NULL,
    body_ct         BLOB NOT NULL,

    -- Optional encrypted map preview (row-level AES-GCM)
    map_nonce       BLOB,
    map_ct          BLOB,
    map_format      TEXT DEFAULT 'ascii',

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
# Migrations (existing installs)
# ---------------------------------------------------------------------

async def _column_exists(db: aiosqlite.Connection, table: str, column: str) -> bool:
    """Return True if `column` is present in `table`."""
    cur = await db.execute(f"PRAGMA table_info({table})")
    rows = await cur.fetchall()
    await cur.close()
    for r in rows:
        # PRAGMA table_info columns: cid, name, type, notnull, default_value, pk
        if len(r) >= 2 and (r[1] == column or (hasattr(r, "keys") and r["name"] == column)):
            return True
    return False


async def migrate_db() -> None:
    """Idempotent migrations for legacy DBs that predate map columns."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys = ON;")
        # Add map columns if they are missing
        add_map_cols = []
        if not await _column_exists(db, "entries", "map_nonce"):
            add_map_cols.append("ALTER TABLE entries ADD COLUMN map_nonce BLOB;")
        if not await _column_exists(db, "entries", "map_ct"):
            add_map_cols.append("ALTER TABLE entries ADD COLUMN map_ct BLOB;")
        if not await _column_exists(db, "entries", "map_format"):
            add_map_cols.append("ALTER TABLE entries ADD COLUMN map_format TEXT DEFAULT 'ascii';")

        add_user_cols = []
        if not await _column_exists(db, "users", "security_question"):
            add_user_cols.append("ALTER TABLE users ADD COLUMN security_question TEXT NOT NULL DEFAULT '';")
        if not await _column_exists(db, "users", "security_answer_hash"):
            add_user_cols.append("ALTER TABLE users ADD COLUMN security_answer_hash TEXT NOT NULL DEFAULT '';")

        for stmt in add_map_cols + add_user_cols:
            await db.execute(stmt)

        if add_map_cols or add_user_cols:
            await db.commit()


# ---------------------------------------------------------------------
# Connection / initialization
# ---------------------------------------------------------------------

async def init_db() -> None:
    """Create tables if they don't exist and run lightweight migrations."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA_SQL)
        await db.commit()
    await migrate_db()


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


async def get_user_security_question(username: str):
    """Fetch the security question for *username*; returns Row or None."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id, security_question FROM users WHERE username = ?",
            (username,),
        )
        row = await cur.fetchone()
        await cur.close()
        return row


async def insert_user(
    username: str,
    pwd_hash: str,
    kek_salt: bytes,
    dek_wrapped: bytes,
    dek_wrap_nonce: bytes,
    security_question: str,
    security_answer_hash: str,
    created_at: str,
) -> None:
    """Insert a newly registered user."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO users (
                username,
                pwd_hash,
                kek_salt,
                dek_wrapped,
                dek_wrap_nonce,
                security_question,
                security_answer_hash,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                username,
                pwd_hash,
                kek_salt,
                dek_wrapped,
                dek_wrap_nonce,
                security_question,
                security_answer_hash,
                created_at,
            ),
        )
        await db.commit()


async def update_user_credentials(
    user_id: int,
    pwd_hash: str,
    kek_salt: bytes,
    dek_wrapped: bytes,
    dek_wrap_nonce: bytes,
) -> None:
    """Update password credentials and wrapped DEK for a user."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE users
               SET pwd_hash = ?,
                   kek_salt = ?,
                   dek_wrapped = ?,
                   dek_wrap_nonce = ?
             WHERE id = ?
            """,
            (pwd_hash, kek_salt, dek_wrapped, dek_wrap_nonce, user_id),
        )
        await db.commit()


# ---------------------------------------------------------------------
# Entries and search terms
# ---------------------------------------------------------------------

async def insert_entry_row(
    user_id: int,
    created_at: str,
    t_nonce: bytes,
    t_ct: bytes,
    b_nonce: bytes,
    b_ct: bytes,
    m_nonce: Optional[bytes] = None,
    m_ct: Optional[bytes] = None,
    m_fmt: str = "ascii",
) -> int:
    """Insert an entry row and return new entry id."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT INTO entries (
                user_id, created_at,
                title_nonce, title_ct,
                body_nonce, body_ct,
                map_nonce, map_ct, map_format
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, created_at, t_nonce, t_ct, b_nonce, b_ct, m_nonce, m_ct, m_fmt),
        )
        await db.commit()
        return cur.lastrowid


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
            """
            SELECT id, created_at, title_nonce, title_ct
              FROM entries
             WHERE user_id = ?
             ORDER BY created_at DESC
            """,
            (user_id,),
        )
        rows = await cur.fetchall()
        await cur.close()
        return rows


async def list_entry_rows_for_user(user_id: int):
    """Return all entry rows for a user, including encrypted map fields."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT id,
                   created_at,
                   title_nonce,
                   title_ct,
                   body_nonce,
                   body_ct,
                   map_nonce,
                   map_ct,
                   map_format
              FROM entries
             WHERE user_id = ?
             ORDER BY created_at DESC
            """,
            (user_id,),
        )
        rows = await cur.fetchall()
        await cur.close()
        return rows


async def get_entry_row(user_id: int, entry_id: int):
    """Return a single entry row (or None) for this user, including optional map fields."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT created_at,
                   title_nonce, title_ct,
                   body_nonce,  body_ct,
                   map_nonce,   map_ct,  map_format
              FROM entries
             WHERE id = ? AND user_id = ?
            """,
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


async def update_entry_row(
    entry_id: int,
    user_id: int,
    title_nonce: bytes,
    title_ct: bytes,
    body_nonce: bytes,
    body_ct: bytes,
) -> None:
    """Update the encrypted title/body for an entry."""
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


async def update_entry_row_with_map(
    entry_id: int,
    user_id: int,
    title_nonce: bytes,
    title_ct: bytes,
    body_nonce: bytes,
    body_ct: bytes,
    map_nonce: Optional[bytes],
    map_ct: Optional[bytes],
    map_format: str = "ascii",
) -> None:
    """Update encrypted title/body/map data for an entry."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE entries
               SET title_nonce = ?,
                   title_ct = ?,
                   body_nonce = ?,
                   body_ct = ?,
                   map_nonce = ?,
                   map_ct = ?,
                   map_format = ?
             WHERE id = ? AND user_id = ?
            """,
            (
                title_nonce,
                title_ct,
                body_nonce,
                body_ct,
                map_nonce,
                map_ct,
                map_format,
                entry_id,
                user_id,
            ),
        )
        await db.commit()


async def update_entry_map_row(
    entry_id: int,
    user_id: int,
    map_nonce: Optional[bytes],
    map_ct: Optional[bytes],
    map_format: str = "ascii",
) -> None:
    """Update the encrypted map payload (optional helper)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE entries
               SET map_nonce = ?, map_ct = ?, map_format = ?
             WHERE id = ? AND user_id = ?
            """,
            (map_nonce, map_ct, map_format, entry_id, user_id),
        )
        await db.commit()


async def delete_entry_row(entry_id: int, user_id: int) -> None:
    """Delete an entry; associated terms are removed via FK cascade."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM entries WHERE id = ? AND user_id = ?",
            (entry_id, user_id),
        )
        await db.commit()


async def delete_entries_for_user(user_id: int) -> None:
    """Delete all entries for a user; terms cascade via foreign keys."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM entries WHERE user_id = ?",
            (user_id,),
        )
        await db.commit()


async def clear_entry_terms(entry_id: int) -> None:
    """Remove all term rows for an entry (used when re-indexing after edit)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM entry_terms WHERE entry_id = ?", (entry_id,))
        await db.commit()
