#! /usr/bin/python3
# -*- coding: utf-8 -*-
"""SQLite schema and async data access for CyberJournal."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, List, Sequence, Tuple, Optional
import os
import aiosqlite

from .errors import DatabaseError, DuplicateUserError, EntryNotFoundError

DB_PATH = os.environ.get("CYBERJOURNAL_DB", "journal_encrypted.sqlite3")


@asynccontextmanager
async def _connect() -> AsyncIterator[aiosqlite.Connection]:
    """Open a DB connection with foreign keys enabled."""
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        yield conn


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

CREATE TABLE IF NOT EXISTS notebooks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    name_nonce      BLOB NOT NULL,
    name_ct         BLOB NOT NULL,
    created_at      TEXT NOT NULL,
    sort_order      INTEGER DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_notebooks_user ON notebooks(user_id);

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

    -- Favorites & word count (Phase 2.1)
    is_favorite     INTEGER DEFAULT 0,
    word_count      INTEGER DEFAULT 0,

    -- Mood & weather (Phase 2.4) — encrypted
    mood_nonce      BLOB,
    mood_ct         BLOB,
    weather_nonce   BLOB,
    weather_ct      BLOB,

    -- Multi-notebook (Phase 2.5)
    notebook_id     INTEGER,

    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (notebook_id) REFERENCES notebooks(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS entry_terms (
    entry_id        INTEGER NOT NULL,
    term_hash       BLOB NOT NULL,
    UNIQUE(entry_id, term_hash),
    FOREIGN KEY (entry_id) REFERENCES entries(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_terms_hash ON entry_terms(term_hash);
CREATE INDEX IF NOT EXISTS idx_terms_entry ON entry_terms(entry_id);
CREATE INDEX IF NOT EXISTS idx_entries_user ON entries(user_id);

-- Encrypted tags with blind-index hash (Phase 2.3)
CREATE TABLE IF NOT EXISTS entry_tags (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id        INTEGER NOT NULL,
    tag_nonce       BLOB NOT NULL,
    tag_ct          BLOB NOT NULL,
    tag_hash        BLOB NOT NULL,
    FOREIGN KEY (entry_id) REFERENCES entries(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tags_entry ON entry_tags(entry_id);
CREATE INDEX IF NOT EXISTS idx_tags_hash ON entry_tags(tag_hash);

-- Entry templates (Phase 2.6)
CREATE TABLE IF NOT EXISTS entry_templates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    name            TEXT NOT NULL,
    title_nonce     BLOB NOT NULL,
    title_ct        BLOB NOT NULL,
    body_nonce      BLOB NOT NULL,
    body_ct         BLOB NOT NULL,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_templates_user ON entry_templates(user_id);

-- Auto-save drafts (Phase 2.10)
CREATE TABLE IF NOT EXISTS drafts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    entry_id        INTEGER,
    title_nonce     BLOB NOT NULL,
    title_ct        BLOB NOT NULL,
    body_nonce      BLOB NOT NULL,
    body_ct         BLOB NOT NULL,
    saved_at        TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (entry_id) REFERENCES entries(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_drafts_user ON drafts(user_id);
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


async def _table_exists(db: aiosqlite.Connection, table: str) -> bool:
    """Return True if `table` exists in the database."""
    cur = await db.execute(
        "SELECT count(*) FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    )
    row = await cur.fetchone()
    await cur.close()
    return row[0] > 0


async def migrate_db() -> None:
    """Idempotent migrations for legacy DBs."""
    async with _connect() as db:
        await db.execute("PRAGMA foreign_keys = ON;")
        stmts = []

        # Map columns on entries
        if not await _column_exists(db, "entries", "map_nonce"):
            stmts.append("ALTER TABLE entries ADD COLUMN map_nonce BLOB;")
        if not await _column_exists(db, "entries", "map_ct"):
            stmts.append("ALTER TABLE entries ADD COLUMN map_ct BLOB;")
        if not await _column_exists(db, "entries", "map_format"):
            stmts.append("ALTER TABLE entries ADD COLUMN map_format TEXT DEFAULT 'ascii';")

        # Security columns on users
        if not await _column_exists(db, "users", "security_question"):
            stmts.append("ALTER TABLE users ADD COLUMN security_question TEXT NOT NULL DEFAULT '';")
        if not await _column_exists(db, "users", "security_answer_hash"):
            stmts.append("ALTER TABLE users ADD COLUMN security_answer_hash TEXT NOT NULL DEFAULT '';")

        # Phase 2 columns on entries
        if not await _column_exists(db, "entries", "is_favorite"):
            stmts.append("ALTER TABLE entries ADD COLUMN is_favorite INTEGER DEFAULT 0;")
        if not await _column_exists(db, "entries", "word_count"):
            stmts.append("ALTER TABLE entries ADD COLUMN word_count INTEGER DEFAULT 0;")
        if not await _column_exists(db, "entries", "mood_nonce"):
            stmts.append("ALTER TABLE entries ADD COLUMN mood_nonce BLOB;")
        if not await _column_exists(db, "entries", "mood_ct"):
            stmts.append("ALTER TABLE entries ADD COLUMN mood_ct BLOB;")
        if not await _column_exists(db, "entries", "weather_nonce"):
            stmts.append("ALTER TABLE entries ADD COLUMN weather_nonce BLOB;")
        if not await _column_exists(db, "entries", "weather_ct"):
            stmts.append("ALTER TABLE entries ADD COLUMN weather_ct BLOB;")
        if not await _column_exists(db, "entries", "notebook_id"):
            stmts.append("ALTER TABLE entries ADD COLUMN notebook_id INTEGER;")

        for stmt in stmts:
            await db.execute(stmt)

        if stmts:
            await db.commit()


# ---------------------------------------------------------------------
# Connection / initialization
# ---------------------------------------------------------------------

async def init_db() -> None:
    """Create tables if they don't exist and run lightweight migrations."""
    async with _connect() as db:
        await db.executescript(SCHEMA_SQL)
        await db.commit()
    await migrate_db()


# ---------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------

async def get_user_by_username(username: str):
    """Fetch a user row by *username*; returns Row or None."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE username = ?", (username,))
        row = await cur.fetchone()
        await cur.close()
        return row


async def get_user_security_question(username: str):
    """Fetch the security question for *username*; returns Row or None."""
    async with _connect() as db:
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
    async with _connect() as conn:
        try:
            await conn.execute(
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
            await conn.commit()
        except aiosqlite.IntegrityError:
            raise DuplicateUserError("Username already taken")


async def update_user_credentials(
    user_id: int,
    pwd_hash: str,
    kek_salt: bytes,
    dek_wrapped: bytes,
    dek_wrap_nonce: bytes,
) -> None:
    """Update password credentials and wrapped DEK for a user."""
    async with _connect() as db:
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
    async with _connect() as db:
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
    async with _connect() as db:
        await db.executemany(
            "INSERT OR IGNORE INTO entry_terms (entry_id, term_hash) VALUES (?, ?)",
            list(pairs),
        )
        await db.commit()


async def list_entry_headers(user_id: int):
    """Return rows of (id, created_at, title_nonce, title_ct) for a user."""
    async with _connect() as db:
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
    """Return all entry rows for a user, including all encrypted fields."""
    async with _connect() as db:
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
                   map_format,
                   mood_nonce,
                   mood_ct,
                   weather_nonce,
                   weather_ct
              FROM entries
             WHERE user_id = ?
             ORDER BY created_at DESC
            """,
            (user_id,),
        )
        rows = await cur.fetchall()
        await cur.close()
        return rows


async def list_all_tags_for_user(user_id: int):
    """Return all tag rows for all entries of a user."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT et.id, et.entry_id, et.tag_nonce, et.tag_ct, et.tag_hash
              FROM entry_tags et
              JOIN entries e ON et.entry_id = e.id
             WHERE e.user_id = ?
            """,
            (user_id,),
        )
        rows = await cur.fetchall()
        await cur.close()
        return rows


async def list_all_drafts_for_user(user_id: int):
    """Return all draft rows for a user."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id, title_nonce, title_ct, body_nonce, body_ct FROM drafts WHERE user_id = ?",
            (user_id,),
        )
        rows = await cur.fetchall()
        await cur.close()
        return rows


async def get_entry_row(user_id: int, entry_id: int):
    """Return a single entry row (or None) for this user, including optional map fields."""
    async with _connect() as db:
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
    async with _connect() as db:
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
    async with _connect() as db:
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
    async with _connect() as db:
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
    async with _connect() as db:
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
    async with _connect() as db:
        await db.execute(
            "DELETE FROM entries WHERE id = ? AND user_id = ?",
            (entry_id, user_id),
        )
        await db.commit()


async def delete_entries_for_user(user_id: int) -> None:
    """Delete all entries for a user; terms cascade via foreign keys."""
    async with _connect() as db:
        await db.execute(
            "DELETE FROM entries WHERE user_id = ?",
            (user_id,),
        )
        await db.commit()


async def change_password_atomically(
    user_id: int,
    pwd_hash: str,
    kek_salt: bytes,
    dek_wrapped: bytes,
    dek_wrap_nonce: bytes,
    entry_updates: List[dict],
    term_updates: List[Tuple[int, List[Tuple[int, bytes]]]],
    tag_updates: List[dict] | None = None,
    notebook_updates: List[dict] | None = None,
    template_updates: List[dict] | None = None,
    draft_updates: List[dict] | None = None,
) -> None:
    """Atomically re-encrypt all data and update user credentials in one transaction."""
    async with _connect() as conn:
        # Entry title/body/map/mood/weather
        for eu in entry_updates:
            await conn.execute(
                """
                UPDATE entries
                   SET title_nonce = ?, title_ct = ?,
                       body_nonce = ?, body_ct = ?,
                       map_nonce = ?, map_ct = ?, map_format = ?,
                       mood_nonce = ?, mood_ct = ?,
                       weather_nonce = ?, weather_ct = ?
                 WHERE id = ? AND user_id = ?
                """,
                (
                    eu["title_nonce"], eu["title_ct"],
                    eu["body_nonce"], eu["body_ct"],
                    eu["map_nonce"], eu["map_ct"], eu["map_format"],
                    eu.get("mood_nonce"), eu.get("mood_ct"),
                    eu.get("weather_nonce"), eu.get("weather_ct"),
                    eu["id"], user_id,
                ),
            )
        # Clear and re-insert terms
        for entry_id, pairs in term_updates:
            await conn.execute("DELETE FROM entry_terms WHERE entry_id = ?", (entry_id,))
            if pairs:
                await conn.executemany(
                    "INSERT OR IGNORE INTO entry_terms (entry_id, term_hash) VALUES (?, ?)",
                    pairs,
                )
        # Tags
        for tu in (tag_updates or []):
            await conn.execute(
                "UPDATE entry_tags SET tag_nonce = ?, tag_ct = ?, tag_hash = ? WHERE id = ?",
                (tu["tag_nonce"], tu["tag_ct"], tu["tag_hash"], tu["id"]),
            )
        # Notebooks
        for nu in (notebook_updates or []):
            await conn.execute(
                "UPDATE notebooks SET name_nonce = ?, name_ct = ? WHERE id = ?",
                (nu["name_nonce"], nu["name_ct"], nu["id"]),
            )
        # Templates
        for tu in (template_updates or []):
            await conn.execute(
                "UPDATE entry_templates SET title_nonce = ?, title_ct = ?, body_nonce = ?, body_ct = ? WHERE id = ?",
                (tu["title_nonce"], tu["title_ct"], tu["body_nonce"], tu["body_ct"], tu["id"]),
            )
        # Drafts
        for du in (draft_updates or []):
            await conn.execute(
                "UPDATE drafts SET title_nonce = ?, title_ct = ?, body_nonce = ?, body_ct = ? WHERE id = ?",
                (du["title_nonce"], du["title_ct"], du["body_nonce"], du["body_ct"], du["id"]),
            )
        # Update user credentials last
        await conn.execute(
            """
            UPDATE users
               SET pwd_hash = ?, kek_salt = ?, dek_wrapped = ?, dek_wrap_nonce = ?
             WHERE id = ?
            """,
            (pwd_hash, kek_salt, dek_wrapped, dek_wrap_nonce, user_id),
        )
        await conn.commit()


async def clear_entry_terms(entry_id: int) -> None:
    """Remove all term rows for an entry (used when re-indexing after edit)."""
    async with _connect() as db:
        await db.execute("DELETE FROM entry_terms WHERE entry_id = ?", (entry_id,))
        await db.commit()


# ---------------------------------------------------------------------
# Favorites (Phase 2.1)
# ---------------------------------------------------------------------

async def toggle_favorite(entry_id: int, user_id: int) -> bool:
    """Toggle is_favorite for an entry. Returns new state."""
    async with _connect() as conn:
        cur = await conn.execute(
            "SELECT is_favorite FROM entries WHERE id = ? AND user_id = ?",
            (entry_id, user_id),
        )
        row = await cur.fetchone()
        await cur.close()
        if not row:
            raise EntryNotFoundError("Entry not found")
        new_val = 0 if row[0] else 1
        await conn.execute(
            "UPDATE entries SET is_favorite = ? WHERE id = ? AND user_id = ?",
            (new_val, entry_id, user_id),
        )
        await conn.commit()
        return bool(new_val)


async def update_word_count(entry_id: int, user_id: int, count: int) -> None:
    """Set word_count for an entry."""
    async with _connect() as conn:
        await conn.execute(
            "UPDATE entries SET word_count = ? WHERE id = ? AND user_id = ?",
            (count, entry_id, user_id),
        )
        await conn.commit()


# ---------------------------------------------------------------------
# Date-range filtering (Phase 2.2)
# ---------------------------------------------------------------------

async def list_entry_headers_in_range(
    user_id: int,
    start: str,
    end: str,
    sort_asc: bool = False,
) -> list:
    """Return entry headers within a date range."""
    order = "ASC" if sort_asc else "DESC"
    async with _connect() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            f"""
            SELECT id, created_at, title_nonce, title_ct, is_favorite, word_count, mood_nonce, mood_ct
              FROM entries
             WHERE user_id = ? AND created_at BETWEEN ? AND ?
             ORDER BY is_favorite DESC, created_at {order}
            """,
            (user_id, start, end),
        )
        rows = await cur.fetchall()
        await cur.close()
        return rows


async def list_entry_headers_sorted(
    user_id: int,
    sort_asc: bool = False,
    notebook_id: Optional[int] = None,
    limit: int = 50,
    offset: int = 0,
) -> list:
    """Return paginated entry headers with optional notebook filter."""
    order = "ASC" if sort_asc else "DESC"
    if notebook_id is not None:
        query = f"""
            SELECT id, created_at, title_nonce, title_ct, is_favorite, word_count, mood_nonce, mood_ct
              FROM entries
             WHERE user_id = ? AND notebook_id = ?
             ORDER BY is_favorite DESC, created_at {order}
             LIMIT ? OFFSET ?
        """
        params = (user_id, notebook_id, limit, offset)
    else:
        query = f"""
            SELECT id, created_at, title_nonce, title_ct, is_favorite, word_count, mood_nonce, mood_ct
              FROM entries
             WHERE user_id = ?
             ORDER BY is_favorite DESC, created_at {order}
             LIMIT ? OFFSET ?
        """
        params = (user_id, limit, offset)
    async with _connect() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(query, params)
        rows = await cur.fetchall()
        await cur.close()
        return rows


async def count_entries(user_id: int, notebook_id: Optional[int] = None) -> int:
    """Return total entry count for pagination."""
    async with _connect() as conn:
        if notebook_id is not None:
            cur = await conn.execute(
                "SELECT count(*) FROM entries WHERE user_id = ? AND notebook_id = ?",
                (user_id, notebook_id),
            )
        else:
            cur = await conn.execute(
                "SELECT count(*) FROM entries WHERE user_id = ?",
                (user_id,),
            )
        row = await cur.fetchone()
        await cur.close()
        return row[0]


# ---------------------------------------------------------------------
# Tags (Phase 2.3)
# ---------------------------------------------------------------------

async def insert_entry_tag(
    entry_id: int, tag_nonce: bytes, tag_ct: bytes, tag_hash: bytes
) -> int:
    """Insert an encrypted tag for an entry."""
    async with _connect() as conn:
        cur = await conn.execute(
            "INSERT INTO entry_tags (entry_id, tag_nonce, tag_ct, tag_hash) VALUES (?, ?, ?, ?)",
            (entry_id, tag_nonce, tag_ct, tag_hash),
        )
        await conn.commit()
        return cur.lastrowid


async def delete_entry_tag(tag_id: int) -> None:
    """Delete a tag by its id."""
    async with _connect() as conn:
        await conn.execute("DELETE FROM entry_tags WHERE id = ?", (tag_id,))
        await conn.commit()


async def get_tags_for_entry(entry_id: int) -> list:
    """Return all tag rows for an entry."""
    async with _connect() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT id, tag_nonce, tag_ct, tag_hash FROM entry_tags WHERE entry_id = ?",
            (entry_id,),
        )
        rows = await cur.fetchall()
        await cur.close()
        return rows


async def get_entry_ids_for_tag_hash(tag_hash: bytes) -> List[int]:
    """Return entry ids that have a tag matching the given hash."""
    async with _connect() as conn:
        cur = await conn.execute(
            "SELECT DISTINCT entry_id FROM entry_tags WHERE tag_hash = ?",
            (tag_hash,),
        )
        rows = await cur.fetchall()
        await cur.close()
        return [int(r[0]) for r in rows]


# ---------------------------------------------------------------------
# Mood & weather (Phase 2.4)
# ---------------------------------------------------------------------

async def update_entry_mood_weather(
    entry_id: int,
    user_id: int,
    mood_nonce: Optional[bytes],
    mood_ct: Optional[bytes],
    weather_nonce: Optional[bytes],
    weather_ct: Optional[bytes],
) -> None:
    """Update encrypted mood and weather for an entry."""
    async with _connect() as conn:
        await conn.execute(
            """
            UPDATE entries
               SET mood_nonce = ?, mood_ct = ?,
                   weather_nonce = ?, weather_ct = ?
             WHERE id = ? AND user_id = ?
            """,
            (mood_nonce, mood_ct, weather_nonce, weather_ct, entry_id, user_id),
        )
        await conn.commit()


async def get_entry_row_full(user_id: int, entry_id: int):
    """Return a full entry row including mood/weather/favorite/notebook."""
    async with _connect() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            """
            SELECT id, created_at,
                   title_nonce, title_ct,
                   body_nonce, body_ct,
                   map_nonce, map_ct, map_format,
                   is_favorite, word_count,
                   mood_nonce, mood_ct,
                   weather_nonce, weather_ct,
                   notebook_id
              FROM entries
             WHERE id = ? AND user_id = ?
            """,
            (entry_id, user_id),
        )
        row = await cur.fetchone()
        await cur.close()
        return row


# ---------------------------------------------------------------------
# Notebooks (Phase 2.5)
# ---------------------------------------------------------------------

async def insert_notebook(
    user_id: int, name_nonce: bytes, name_ct: bytes, created_at: str
) -> int:
    """Create a new notebook."""
    async with _connect() as conn:
        cur = await conn.execute(
            """
            INSERT INTO notebooks (user_id, name_nonce, name_ct, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, name_nonce, name_ct, created_at),
        )
        await conn.commit()
        return cur.lastrowid


async def list_notebooks(user_id: int) -> list:
    """Return all notebooks for a user."""
    async with _connect() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT id, name_nonce, name_ct, created_at, sort_order FROM notebooks WHERE user_id = ? ORDER BY sort_order, created_at",
            (user_id,),
        )
        rows = await cur.fetchall()
        await cur.close()
        return rows


async def delete_notebook(notebook_id: int, user_id: int) -> None:
    """Delete a notebook (entries are unlinked via SET NULL)."""
    async with _connect() as conn:
        await conn.execute(
            "DELETE FROM notebooks WHERE id = ? AND user_id = ?",
            (notebook_id, user_id),
        )
        await conn.commit()


async def set_entry_notebook(entry_id: int, user_id: int, notebook_id: Optional[int]) -> None:
    """Assign an entry to a notebook (or None to unassign)."""
    async with _connect() as conn:
        await conn.execute(
            "UPDATE entries SET notebook_id = ? WHERE id = ? AND user_id = ?",
            (notebook_id, entry_id, user_id),
        )
        await conn.commit()


# ---------------------------------------------------------------------
# Templates (Phase 2.6)
# ---------------------------------------------------------------------

async def insert_template(
    user_id: int,
    name: str,
    title_nonce: bytes,
    title_ct: bytes,
    body_nonce: bytes,
    body_ct: bytes,
    created_at: str,
) -> int:
    """Create a new entry template."""
    async with _connect() as conn:
        cur = await conn.execute(
            """
            INSERT INTO entry_templates (user_id, name, title_nonce, title_ct, body_nonce, body_ct, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, name, title_nonce, title_ct, body_nonce, body_ct, created_at),
        )
        await conn.commit()
        return cur.lastrowid


async def list_templates(user_id: int) -> list:
    """Return all templates for a user."""
    async with _connect() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT id, name, title_nonce, title_ct, body_nonce, body_ct FROM entry_templates WHERE user_id = ? ORDER BY name",
            (user_id,),
        )
        rows = await cur.fetchall()
        await cur.close()
        return rows


async def get_template(template_id: int, user_id: int):
    """Return a single template row."""
    async with _connect() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT id, name, title_nonce, title_ct, body_nonce, body_ct FROM entry_templates WHERE id = ? AND user_id = ?",
            (template_id, user_id),
        )
        row = await cur.fetchone()
        await cur.close()
        return row


async def delete_template(template_id: int, user_id: int) -> None:
    """Delete a template."""
    async with _connect() as conn:
        await conn.execute(
            "DELETE FROM entry_templates WHERE id = ? AND user_id = ?",
            (template_id, user_id),
        )
        await conn.commit()


# ---------------------------------------------------------------------
# Calendar data (Phase 2.7)
# ---------------------------------------------------------------------

async def get_entry_dates_for_month(user_id: int, year: int, month: int) -> list:
    """Return list of (date_str, count) for entries in a given month."""
    start = f"{year:04d}-{month:02d}-01"
    if month == 12:
        end = f"{year + 1:04d}-01-01"
    else:
        end = f"{year:04d}-{month + 1:02d}-01"
    async with _connect() as conn:
        cur = await conn.execute(
            """
            SELECT substr(created_at, 1, 10) as day, count(*) as cnt
              FROM entries
             WHERE user_id = ? AND created_at >= ? AND created_at < ?
             GROUP BY day
            """,
            (user_id, start, end),
        )
        rows = await cur.fetchall()
        await cur.close()
        return rows


# ---------------------------------------------------------------------
# Drafts (Phase 2.10)
# ---------------------------------------------------------------------

async def upsert_draft(
    user_id: int,
    entry_id: Optional[int],
    title_nonce: bytes,
    title_ct: bytes,
    body_nonce: bytes,
    body_ct: bytes,
    saved_at: str,
) -> int:
    """Insert or update a draft for the given user/entry_id pair."""
    async with _connect() as conn:
        if entry_id is not None:
            # Check if draft exists for this entry
            cur = await conn.execute(
                "SELECT id FROM drafts WHERE user_id = ? AND entry_id = ?",
                (user_id, entry_id),
            )
        else:
            cur = await conn.execute(
                "SELECT id FROM drafts WHERE user_id = ? AND entry_id IS NULL",
                (user_id,),
            )
        existing = await cur.fetchone()
        await cur.close()

        if existing:
            await conn.execute(
                """
                UPDATE drafts SET title_nonce = ?, title_ct = ?,
                       body_nonce = ?, body_ct = ?, saved_at = ?
                 WHERE id = ?
                """,
                (title_nonce, title_ct, body_nonce, body_ct, saved_at, existing[0]),
            )
            await conn.commit()
            return existing[0]
        else:
            cur = await conn.execute(
                """
                INSERT INTO drafts (user_id, entry_id, title_nonce, title_ct, body_nonce, body_ct, saved_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, entry_id, title_nonce, title_ct, body_nonce, body_ct, saved_at),
            )
            await conn.commit()
            return cur.lastrowid


async def get_draft(user_id: int, entry_id: Optional[int] = None):
    """Return the draft for a user/entry_id pair, or None."""
    async with _connect() as conn:
        conn.row_factory = aiosqlite.Row
        if entry_id is not None:
            cur = await conn.execute(
                "SELECT * FROM drafts WHERE user_id = ? AND entry_id = ?",
                (user_id, entry_id),
            )
        else:
            cur = await conn.execute(
                "SELECT * FROM drafts WHERE user_id = ? AND entry_id IS NULL",
                (user_id,),
            )
        row = await cur.fetchone()
        await cur.close()
        return row


async def delete_draft(draft_id: int) -> None:
    """Delete a draft by id."""
    async with _connect() as conn:
        await conn.execute("DELETE FROM drafts WHERE id = ?", (draft_id,))
        await conn.commit()


async def delete_drafts_for_user(user_id: int) -> None:
    """Delete all drafts for a user."""
    async with _connect() as conn:
        await conn.execute("DELETE FROM drafts WHERE user_id = ?", (user_id,))
        await conn.commit()
