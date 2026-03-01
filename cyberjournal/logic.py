# -*- coding: utf-8 -*-
"""Application logic that composes DB and crypto layers.

This module provides the public API used by the UI. It does not contain any
Textual UI code. All side effects (DB + config I/O) are explicit and local.
"""
from __future__ import annotations

from typing import Dict, Set, Tuple, List
from pathlib import Path
from datetime import datetime, timezone
import json
import os
import secrets # if not already imported
import shutil
from cyberjournal.map import text_to_map, render_colored_map
from argon2.exceptions import VerifyMismatchError
import logging

logger = logging.getLogger(__name__)

from . import db
from .errors import EntryNotFoundError
from .crypto import (
    PH,
    DEK_LEN,
    SessionKeys,
    scrypt_kdf,
    hkdf_derive,
    aesgcm_encrypt,
    aesgcm_decrypt,
    normalize_tokens,
    hmac_token,
    HKDF_INFO_ENC,
    HKDF_INFO_HMAC,
)

# ---------------------------------------------------------------------
# Config management (JSON on disk)
# ---------------------------------------------------------------------

APP_NAME = "cyberjournal"

DEFAULT_CONFIG: Dict[str, object] = {
    "active_theme": "vt220_green",
    "ascii_art_enabled": True,
    # Simple default that renders safely in a Textual Static with markup=False
    "ascii_art": "CYBER//JOURNAL\n",
}

def _config_dir() -> Path:
    """Return the config directory path for this platform."""
    if os.name == "nt":
        base = os.environ.get("APPDATA", os.path.expanduser("~\\AppData\\Roaming"))
        return Path(base) / APP_NAME
    base = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    return Path(base) / APP_NAME

def _config_path() -> Path:
    return _config_dir() / "config.json"

def load_config() -> Dict[str, object]:
    """Load the merged configuration (defaults + file)."""
    path = _config_path()
    if not path.exists():
        save_config(DEFAULT_CONFIG)
        return json.loads(json.dumps(DEFAULT_CONFIG))
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    merged.update(data)
    return merged

def save_config(cfg: Dict[str, object]) -> None:
    """Persist *cfg* to the JSON config file."""
    _config_dir().mkdir(parents=True, exist_ok=True)
    with _config_path().open("w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


# ---------------------------------------------------------------------
# DB bridge
# ---------------------------------------------------------------------

async def init_db() -> None:
    """Initialize the SQLite database (create tables on first run)."""
    await db.init_db()


# ---------------------------------------------------------------------
# Backup helpers
# ---------------------------------------------------------------------


def _backup_database() -> Path:
    """Create a timestamped backup copy of the SQLite database."""
    db_path = Path(db.DB_PATH).expanduser()
    if not db_path.is_absolute():
        db_path = (Path.cwd() / db_path).resolve()
    if not db_path.exists():
        raise ValueError("Database file not found for backup")

    backups_dir = db_path.parent / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = backups_dir / f"{db_path.name}.bak-{timestamp}"
    shutil.copy2(db_path, backup_path)
    return backup_path


# ---------------------------------------------------------------------
# Auth and key management
# ---------------------------------------------------------------------

async def register_user(
    username: str,
    password: str,
    security_question: str,
    security_answer: str,
) -> None:
    """Register a new user and store a wrapped DEK in the DB."""
    created_at = datetime.now(timezone.utc).isoformat()
    pwd_hash = PH.hash(password)

    if not security_question.strip() or not security_answer.strip():
        raise ValueError("Security question and answer are required")

    answer_hash = PH.hash(security_answer)
    dek = secrets.token_bytes(DEK_LEN)
    kek_salt = secrets.token_bytes(16)
    kek = scrypt_kdf(password, kek_salt, 32)
    wrap_key = hkdf_derive(kek, b"wrap-key", 32)
    nonce, wrapped = aesgcm_encrypt(wrap_key, dek, aad=username.encode())

    await db.insert_user(
        username,
        pwd_hash,
        kek_salt,
        wrapped,
        nonce,
        security_question,
        answer_hash,
        created_at,
    )

async def login_user(username: str, password: str) -> SessionKeys:
    """Authenticate user and return derived session keys."""
    row = await db.get_user_by_username(username)
    if not row:
        raise ValueError("User not found")
    try:
        PH.verify(row["pwd_hash"], password)
    except VerifyMismatchError as exc:
        raise ValueError("Invalid password") from exc

    kek = scrypt_kdf(password, row["kek_salt"], 32)
    wrap_key = hkdf_derive(kek, b"wrap-key", 32)
    dek = aesgcm_decrypt(
        wrap_key,
        row["dek_wrap_nonce"],
        row["dek_wrapped"],
        aad=row["username"].encode(),
    )

    enc_key = hkdf_derive(dek, HKDF_INFO_ENC, 32)
    search_key = hkdf_derive(dek, HKDF_INFO_HMAC, 32)
    return SessionKeys(
        user_id=row["id"],
        username=row["username"],
        dek=dek,
        enc_key=enc_key,
        search_key=search_key,
    )


async def get_security_question(username: str) -> str:
    """Return the security question for *username* or raise if missing."""
    row = await db.get_user_security_question(username)
    if not row:
        raise ValueError("User not found")
    question = str(row["security_question"] or "").strip()
    if not question:
        raise ValueError("Security question not set")
    return question


async def change_password_logged_in(
    sess: SessionKeys,
    current_password: str,
    new_password: str,
) -> SessionKeys:
    """Re-encrypt all data with a new password and DEK."""
    if not current_password:
        raise ValueError("Current password required")
    if not new_password:
        raise ValueError("New password required")

    row = await db.get_user_by_username(sess.username)
    if not row:
        raise ValueError("User not found")
    if row["id"] != sess.user_id:
        raise ValueError("Session mismatch")
    try:
        PH.verify(row["pwd_hash"], current_password)
    except VerifyMismatchError as exc:
        raise ValueError("Invalid password") from exc

    _backup_database()

    new_dek = secrets.token_bytes(DEK_LEN)
    new_pwd_hash = PH.hash(new_password)
    new_kek_salt = secrets.token_bytes(16)
    new_kek = scrypt_kdf(new_password, new_kek_salt, 32)
    new_wrap_key = hkdf_derive(new_kek, b"wrap-key", 32)
    new_nonce, new_wrapped = aesgcm_encrypt(new_wrap_key, new_dek, aad=sess.username.encode())

    new_enc_key = hkdf_derive(new_dek, HKDF_INFO_ENC, 32)
    new_search_key = hkdf_derive(new_dek, HKDF_INFO_HMAC, 32)

    # Re-encrypt all data in memory first, then commit atomically
    aad = sess.username.encode()

    def _reenc(old_nonce, old_ct):
        """Decrypt with old key, re-encrypt with new key. Returns (nonce, ct)."""
        plaintext = aesgcm_decrypt(sess.enc_key, old_nonce, old_ct, aad=aad)
        return aesgcm_encrypt(new_enc_key, plaintext, aad=aad)

    # --- Entries ---
    rows = await db.list_entry_rows_for_user(sess.user_id)
    entry_updates = []
    term_updates = []
    for entry in rows:
        title = aesgcm_decrypt(sess.enc_key, entry["title_nonce"], entry["title_ct"], aad=aad).decode()
        body = aesgcm_decrypt(sess.enc_key, entry["body_nonce"], entry["body_ct"], aad=aad).decode()

        t_nonce, t_ct = aesgcm_encrypt(new_enc_key, title.encode(), aad=aad)
        b_nonce, b_ct = aesgcm_encrypt(new_enc_key, body.encode(), aad=aad)

        map_nonce = map_ct = None
        map_format = entry["map_format"] or "ascii"
        if entry["map_ct"]:
            map_nonce, map_ct = _reenc(entry["map_nonce"], entry["map_ct"])

        mood_nonce = mood_ct = None
        if entry["mood_ct"]:
            mood_nonce, mood_ct = _reenc(entry["mood_nonce"], entry["mood_ct"])

        weather_nonce = weather_ct = None
        if entry["weather_ct"]:
            weather_nonce, weather_ct = _reenc(entry["weather_nonce"], entry["weather_ct"])

        entry_updates.append({
            "id": entry["id"],
            "title_nonce": t_nonce, "title_ct": t_ct,
            "body_nonce": b_nonce, "body_ct": b_ct,
            "map_nonce": map_nonce, "map_ct": map_ct, "map_format": map_format,
            "mood_nonce": mood_nonce, "mood_ct": mood_ct,
            "weather_nonce": weather_nonce, "weather_ct": weather_ct,
        })

        terms: Set[str] = set(normalize_tokens(title) + normalize_tokens(body))
        pairs = [(entry["id"], hmac_token(new_search_key, t)) for t in terms]
        term_updates.append((entry["id"], pairs))

    # --- Tags ---
    tag_rows = await db.list_all_tags_for_user(sess.user_id)
    tag_updates = []
    for tag in tag_rows:
        tag_text = aesgcm_decrypt(sess.enc_key, tag["tag_nonce"], tag["tag_ct"], aad=aad).decode()
        new_tag_nonce, new_tag_ct = aesgcm_encrypt(new_enc_key, tag_text.encode(), aad=aad)
        new_tag_hash = hmac_token(new_search_key, tag_text.lower())
        tag_updates.append({
            "id": tag["id"],
            "tag_nonce": new_tag_nonce, "tag_ct": new_tag_ct, "tag_hash": new_tag_hash,
        })

    # --- Notebooks ---
    nb_rows = await db.list_notebooks(sess.user_id)
    notebook_updates = []
    for nb in nb_rows:
        nb_name = aesgcm_decrypt(sess.enc_key, nb["name_nonce"], nb["name_ct"], aad=aad).decode()
        new_nb_nonce, new_nb_ct = aesgcm_encrypt(new_enc_key, nb_name.encode(), aad=aad)
        notebook_updates.append({
            "id": nb["id"], "name_nonce": new_nb_nonce, "name_ct": new_nb_ct,
        })

    # --- Templates ---
    tpl_rows = await db.list_templates(sess.user_id)
    template_updates = []
    for tpl in tpl_rows:
        tpl_title = aesgcm_decrypt(sess.enc_key, tpl["title_nonce"], tpl["title_ct"], aad=aad).decode()
        tpl_body = aesgcm_decrypt(sess.enc_key, tpl["body_nonce"], tpl["body_ct"], aad=aad).decode()
        tn, tc = aesgcm_encrypt(new_enc_key, tpl_title.encode(), aad=aad)
        bn, bc = aesgcm_encrypt(new_enc_key, tpl_body.encode(), aad=aad)
        template_updates.append({
            "id": tpl["id"], "title_nonce": tn, "title_ct": tc, "body_nonce": bn, "body_ct": bc,
        })

    # --- Drafts ---
    draft_rows = await db.list_all_drafts_for_user(sess.user_id)
    draft_updates = []
    for dr in draft_rows:
        dr_title = aesgcm_decrypt(sess.enc_key, dr["title_nonce"], dr["title_ct"], aad=aad).decode()
        dr_body = aesgcm_decrypt(sess.enc_key, dr["body_nonce"], dr["body_ct"], aad=aad).decode()
        dn, dc = aesgcm_encrypt(new_enc_key, dr_title.encode(), aad=aad)
        dbn, dbc = aesgcm_encrypt(new_enc_key, dr_body.encode(), aad=aad)
        draft_updates.append({
            "id": dr["id"], "title_nonce": dn, "title_ct": dc, "body_nonce": dbn, "body_ct": dbc,
        })

    await db.change_password_atomically(
        sess.user_id,
        new_pwd_hash,
        new_kek_salt,
        new_wrapped,
        new_nonce,
        entry_updates,
        term_updates,
        tag_updates=tag_updates,
        notebook_updates=notebook_updates,
        template_updates=template_updates,
        draft_updates=draft_updates,
    )

    return SessionKeys(
        user_id=sess.user_id,
        username=sess.username,
        dek=new_dek,
        enc_key=new_enc_key,
        search_key=new_search_key,
    )


async def reset_password_with_security_answer(
    username: str,
    security_answer: str,
    new_password: str,
) -> None:
    """Reset password after security answer; wipes all existing entries."""
    if not security_answer:
        raise ValueError("Security answer required")
    if not new_password:
        raise ValueError("New password required")

    row = await db.get_user_by_username(username)
    if not row:
        raise ValueError("User not found")
    try:
        PH.verify(row["security_answer_hash"], security_answer)
    except VerifyMismatchError as exc:
        raise ValueError("Invalid security answer") from exc

    _backup_database()
    await db.delete_entries_for_user(row["id"])

    new_dek = secrets.token_bytes(DEK_LEN)
    new_pwd_hash = PH.hash(new_password)
    new_kek_salt = secrets.token_bytes(16)
    new_kek = scrypt_kdf(new_password, new_kek_salt, 32)
    new_wrap_key = hkdf_derive(new_kek, b"wrap-key", 32)
    new_nonce, new_wrapped = aesgcm_encrypt(new_wrap_key, new_dek, aad=username.encode())

    await db.update_user_credentials(
        row["id"],
        new_pwd_hash,
        new_kek_salt,
        new_wrapped,
        new_nonce,
    )


# ---------------------------------------------------------------------
# Entries (encrypted title/body) + blind index
# ---------------------------------------------------------------------

def _word_count(text: str) -> int:
    """Count words in plaintext."""
    return len(text.split())


async def add_entry(
    sess: SessionKeys,
    title: str,
    body: str,
    mood: str = "",
    weather: str = "",
    notebook_id: int | None = None,
) -> int:
    """Insert an encrypted entry; return new entry id."""
    created_at = datetime.now(timezone.utc).isoformat()

    t_nonce, t_ct = aesgcm_encrypt(sess.enc_key, title.encode(), aad=sess.username.encode())
    b_nonce, b_ct = aesgcm_encrypt(sess.enc_key, body.encode(), aad=sess.username.encode())

    map_text, map_fmt = _render_entry_map_text(title, body, fmt="ascii", max_side=32)
    m_nonce, m_ct = aesgcm_encrypt(sess.enc_key, map_text.encode("utf-8"), aad=sess.username.encode())

    eid = await db.insert_entry_row(
        sess.user_id,
        created_at,
        t_nonce, t_ct,
        b_nonce, b_ct,
        m_nonce, m_ct, map_fmt,
    )

    # Set word count
    wc = _word_count(f"{title} {body}")
    await db.update_word_count(eid, sess.user_id, wc)

    # Set notebook if provided
    if notebook_id is not None:
        await db.set_entry_notebook(eid, sess.user_id, notebook_id)

    # Encrypt and store mood/weather if provided
    if mood or weather:
        mood_nonce = mood_ct = weather_nonce = weather_ct = None
        if mood:
            mood_nonce, mood_ct = aesgcm_encrypt(sess.enc_key, mood.encode(), aad=sess.username.encode())
        if weather:
            weather_nonce, weather_ct = aesgcm_encrypt(sess.enc_key, weather.encode(), aad=sess.username.encode())
        await db.update_entry_mood_weather(eid, sess.user_id, mood_nonce, mood_ct, weather_nonce, weather_ct)

    # Blind index
    terms: Set[str] = set(normalize_tokens(title) + normalize_tokens(body))
    pairs = [(eid, hmac_token(sess.search_key, t)) for t in terms]
    await db.insert_entry_terms(pairs)

    # World integration hook
    try:
        from cyberjournal.world.hooks import on_entry_created
        await on_entry_created(eid, title, body, word_count=wc, mood=mood)
    except Exception:
        logger.debug("World hook skipped", exc_info=True)

    return eid


async def update_entry(sess: SessionKeys, entry_id: int,
                     new_title: str, new_body: str) -> None:
    """Re-encrypt title/body, regenerate map, update word count and blind index."""
    t_nonce, t_ct = aesgcm_encrypt(sess.enc_key, new_title.encode(), aad=sess.username.encode())
    b_nonce, b_ct = aesgcm_encrypt(sess.enc_key, new_body.encode(),  aad=sess.username.encode())

    # Regenerate map
    map_text, map_fmt = _render_entry_map_text(new_title, new_body, fmt="ascii", max_side=32)
    m_nonce, m_ct = aesgcm_encrypt(sess.enc_key, map_text.encode("utf-8"), aad=sess.username.encode())

    await db.update_entry_row_with_map(
        entry_id, sess.user_id,
        t_nonce, t_ct, b_nonce, b_ct,
        m_nonce, m_ct, map_fmt,
    )

    # Update word count
    wc = _word_count(f"{new_title} {new_body}")
    await db.update_word_count(entry_id, sess.user_id, wc)

    # Rebuild blind index terms
    await db.clear_entry_terms(entry_id)
    terms: Set[str] = set(normalize_tokens(new_title) + normalize_tokens(new_body))
    pairs: list[Tuple[int, bytes]] = [(entry_id, hmac_token(sess.search_key, t)) for t in terms]
    if pairs:
        await db.insert_entry_terms(pairs)

    # World integration hook
    try:
        from cyberjournal.world.hooks import on_entry_edited
        await on_entry_edited(entry_id, new_title, new_body)
    except Exception:
        logger.debug("World hook skipped", exc_info=True)


async def delete_entry(sess: SessionKeys, entry_id: int) -> None:
    await db.delete_entry_row(entry_id, sess.user_id)
    # World integration hook
    try:
        from cyberjournal.world.hooks import on_entry_deleted
        await on_entry_deleted(entry_id)
    except Exception:
        logger.debug("World hook skipped", exc_info=True)


async def rebuild_world(sess: SessionKeys) -> int:
    """Rebuild the world from all existing entries. Returns count of entries processed."""
    from cyberjournal.world.hooks import on_entry_created

    entries = await list_entries(sess)
    count = 0
    for eid, created_at, title in entries:
        # Check if entry already has a chunk in the world
        from cyberjournal.world.grid import get_chunk_placements
        placements = await get_chunk_placements()
        if eid in placements:
            continue

        # Get full entry body for world generation
        _, _, body = await get_entry(sess, eid)
        # Get mood if available
        mood = ""
        try:
            full = await get_entry_full(sess, eid)
            mood = full.get("mood", "")
        except Exception:
            pass
        wc = _word_count(f"{title} {body}")
        await on_entry_created(eid, title, body, word_count=wc, mood=mood)
        count += 1

    return count


async def list_entries(sess: SessionKeys) -> List[Tuple[int, str, str]]:
    """Return list of (id, created_at iso, decrypted_title)."""
    rows = await db.list_entry_headers(sess.user_id)
    out: List[Tuple[int, str, str]] = []
    for r in rows:
        title = aesgcm_decrypt(
            sess.enc_key, r["title_nonce"], r["title_ct"], aad=sess.username.encode()
        ).decode()
        out.append((r["id"], r["created_at"], title))
    return out

async def get_entry(sess: SessionKeys, entry_id: int) -> Tuple[str, str, str]:
    """Return (created_at iso, title, body) for *entry_id* or error."""
    r = await db.get_entry_row(sess.user_id, entry_id)
    if not r:
        raise EntryNotFoundError("Entry not found")
    title = aesgcm_decrypt(
        sess.enc_key, r["title_nonce"], r["title_ct"], aad=sess.username.encode()
    ).decode()
    body = aesgcm_decrypt(
        sess.enc_key, r["body_nonce"], r["body_ct"], aad=sess.username.encode()
    ).decode()
    return r["created_at"], title, body

async def search_entries(sess: SessionKeys, query: str) -> List[int]:
    """Return entry ids that contain ALL tokens in *query* (AND)."""
    tokens = [t for t in normalize_tokens(query) if t]
    if not tokens:
        return []
    id_sets = []
    for token in tokens:
        th = hmac_token(sess.search_key, token)
        ids = await db.get_entry_ids_for_term(th)
        id_sets.append(set(ids))
    return sorted(set.intersection(*id_sets), reverse=True) if id_sets else []

def _render_entry_map_text(title: str, body: str, *, fmt: str = "utf", max_side: int = 64) -> tuple[str, str]:
    """
    Create a small, deterministic map string for storage alongside the entry.
    - fmt: one of {"ascii","utf"} — we store this as map_format in DB.
    - max_side: cap size so entries don't explode the DB.
    Returns: (map_text, map_format)
    """
    text = f"{title}\n{body}".strip()
    # Generate a capped-size map for TUI-friendly storage
    openings, types, costs, legend = text_to_map(text, width=max_side, height=12)

    # We store a *plain* (no ANSI color) rendering in DB to keep it small & portable.
    charset = "ascii" if fmt == "ascii" else "utf"
    map_text = render_colored_map(types, legend, charset=charset, color=False, border=False)
    return map_text, fmt

async def get_entry_with_map(sess: SessionKeys, entry_id: int):
    """Return (created_at, title, body, map_text, map_format) for one entry."""
    row = await db.get_entry_row(sess.user_id, entry_id)
    if not row:
        raise EntryNotFoundError("Entry not found")

    title = aesgcm_decrypt(sess.enc_key, row["title_nonce"], row["title_ct"], aad=sess.username.encode()).decode()
    body  = aesgcm_decrypt(sess.enc_key, row["body_nonce"],  row["body_ct"],  aad=sess.username.encode()).decode()

    map_text = ""
    map_fmt  = (row["map_format"] or "ascii") if "map_format" in row.keys() else "ascii"
    if "map_ct" in row.keys() and row["map_ct"]:
        map_text = aesgcm_decrypt(sess.enc_key, row["map_nonce"], row["map_ct"], aad=sess.username.encode()).decode()

    return row["created_at"], title, body, map_text, map_fmt


async def get_entry_full(sess: SessionKeys, entry_id: int) -> dict:
    """Return a full entry dict with all decrypted fields."""
    row = await db.get_entry_row_full(sess.user_id, entry_id)
    if not row:
        raise EntryNotFoundError("Entry not found")

    aad = sess.username.encode()
    result = {
        "id": row["id"],
        "created_at": row["created_at"],
        "title": aesgcm_decrypt(sess.enc_key, row["title_nonce"], row["title_ct"], aad=aad).decode(),
        "body": aesgcm_decrypt(sess.enc_key, row["body_nonce"], row["body_ct"], aad=aad).decode(),
        "is_favorite": bool(row["is_favorite"]),
        "word_count": row["word_count"] or 0,
        "notebook_id": row["notebook_id"],
        "mood": "",
        "weather": "",
        "map_text": "",
        "map_format": row["map_format"] or "ascii",
    }

    if row["map_ct"]:
        result["map_text"] = aesgcm_decrypt(sess.enc_key, row["map_nonce"], row["map_ct"], aad=aad).decode()
    if row["mood_ct"]:
        result["mood"] = aesgcm_decrypt(sess.enc_key, row["mood_nonce"], row["mood_ct"], aad=aad).decode()
    if row["weather_ct"]:
        result["weather"] = aesgcm_decrypt(sess.enc_key, row["weather_nonce"], row["weather_ct"], aad=aad).decode()

    return result


# ---------------------------------------------------------------------
# Favorites (Phase 2.1)
# ---------------------------------------------------------------------

async def toggle_favorite(sess: SessionKeys, entry_id: int) -> bool:
    """Toggle favorite status for an entry. Returns new state."""
    return await db.toggle_favorite(entry_id, sess.user_id)


# ---------------------------------------------------------------------
# Date-range filtering (Phase 2.2)
# ---------------------------------------------------------------------

async def list_entries_in_range(
    sess: SessionKeys, start: str, end: str, sort_asc: bool = False
) -> List[Tuple[int, str, str, bool, int]]:
    """Return entries in date range: (id, created_at, title, is_favorite, word_count)."""
    rows = await db.list_entry_headers_in_range(sess.user_id, start, end, sort_asc)
    out = []
    for r in rows:
        title = aesgcm_decrypt(
            sess.enc_key, r["title_nonce"], r["title_ct"], aad=sess.username.encode()
        ).decode()
        out.append((r["id"], r["created_at"], title, bool(r["is_favorite"]), r["word_count"] or 0))
    return out


async def list_entries_paginated(
    sess: SessionKeys,
    sort_asc: bool = False,
    notebook_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> List[Tuple[int, str, str, bool, int, str]]:
    """Return paginated entries: (id, created_at, title, is_favorite, word_count, mood)."""
    rows = await db.list_entry_headers_sorted(sess.user_id, sort_asc, notebook_id, limit, offset)
    aad = sess.username.encode()
    out = []
    for r in rows:
        title = aesgcm_decrypt(sess.enc_key, r["title_nonce"], r["title_ct"], aad=aad).decode()
        mood = ""
        if r["mood_ct"]:
            try:
                mood = aesgcm_decrypt(sess.enc_key, r["mood_nonce"], r["mood_ct"], aad=aad).decode()
            except Exception:
                pass
        out.append((r["id"], r["created_at"], title, bool(r["is_favorite"]), r["word_count"] or 0, mood))
    return out


async def count_entries(sess: SessionKeys, notebook_id: int | None = None) -> int:
    """Return total entry count."""
    return await db.count_entries(sess.user_id, notebook_id)


# ---------------------------------------------------------------------
# Tags (Phase 2.3)
# ---------------------------------------------------------------------

async def add_tag(sess: SessionKeys, entry_id: int, tag: str) -> int:
    """Add an encrypted tag to an entry. Returns tag id."""
    tag = tag.strip().lower()
    if not tag:
        raise ValueError("Tag cannot be empty")
    aad = sess.username.encode()
    tag_nonce, tag_ct = aesgcm_encrypt(sess.enc_key, tag.encode(), aad=aad)
    tag_hash = hmac_token(sess.search_key, f"tag:{tag}")
    return await db.insert_entry_tag(entry_id, tag_nonce, tag_ct, tag_hash)


async def remove_tag(sess: SessionKeys, tag_id: int) -> None:
    """Remove a tag by id."""
    await db.delete_entry_tag(tag_id)


async def list_tags(sess: SessionKeys, entry_id: int) -> List[Tuple[int, str]]:
    """Return list of (tag_id, decrypted_tag) for an entry."""
    rows = await db.get_tags_for_entry(entry_id)
    aad = sess.username.encode()
    return [
        (r["id"], aesgcm_decrypt(sess.enc_key, r["tag_nonce"], r["tag_ct"], aad=aad).decode())
        for r in rows
    ]


async def search_by_tag(sess: SessionKeys, tag: str) -> List[int]:
    """Return entry ids that have the given tag."""
    tag = tag.strip().lower()
    if not tag:
        return []
    tag_hash = hmac_token(sess.search_key, f"tag:{tag}")
    return await db.get_entry_ids_for_tag_hash(tag_hash)


# ---------------------------------------------------------------------
# Mood & weather (Phase 2.4)
# ---------------------------------------------------------------------

MOOD_CHOICES = ["happy", "sad", "neutral", "anxious", "energetic", "calm"]


async def set_mood_weather(
    sess: SessionKeys, entry_id: int, mood: str = "", weather: str = ""
) -> None:
    """Set encrypted mood and weather on an entry."""
    aad = sess.username.encode()
    mood_nonce = mood_ct = weather_nonce = weather_ct = None
    if mood:
        mood_nonce, mood_ct = aesgcm_encrypt(sess.enc_key, mood.encode(), aad=aad)
    if weather:
        weather_nonce, weather_ct = aesgcm_encrypt(sess.enc_key, weather.encode(), aad=aad)
    await db.update_entry_mood_weather(
        entry_id, sess.user_id,
        mood_nonce, mood_ct, weather_nonce, weather_ct,
    )


# ---------------------------------------------------------------------
# Notebooks (Phase 2.5)
# ---------------------------------------------------------------------

async def create_notebook(sess: SessionKeys, name: str) -> int:
    """Create a new encrypted notebook. Returns notebook id."""
    if not name.strip():
        raise ValueError("Notebook name cannot be empty")
    aad = sess.username.encode()
    name_nonce, name_ct = aesgcm_encrypt(sess.enc_key, name.encode(), aad=aad)
    created_at = datetime.now(timezone.utc).isoformat()
    return await db.insert_notebook(sess.user_id, name_nonce, name_ct, created_at)


async def list_notebooks(sess: SessionKeys) -> List[Tuple[int, str]]:
    """Return list of (notebook_id, decrypted_name)."""
    rows = await db.list_notebooks(sess.user_id)
    aad = sess.username.encode()
    return [
        (r["id"], aesgcm_decrypt(sess.enc_key, r["name_nonce"], r["name_ct"], aad=aad).decode())
        for r in rows
    ]


async def delete_notebook(sess: SessionKeys, notebook_id: int) -> None:
    """Delete a notebook (entries become unassigned)."""
    await db.delete_notebook(notebook_id, sess.user_id)


async def assign_entry_notebook(sess: SessionKeys, entry_id: int, notebook_id: int | None) -> None:
    """Assign or unassign an entry to/from a notebook."""
    await db.set_entry_notebook(entry_id, sess.user_id, notebook_id)


# ---------------------------------------------------------------------
# Templates (Phase 2.6)
# ---------------------------------------------------------------------

async def create_template(sess: SessionKeys, name: str, title: str, body: str) -> int:
    """Create an encrypted entry template."""
    if not name.strip():
        raise ValueError("Template name cannot be empty")
    aad = sess.username.encode()
    t_nonce, t_ct = aesgcm_encrypt(sess.enc_key, title.encode(), aad=aad)
    b_nonce, b_ct = aesgcm_encrypt(sess.enc_key, body.encode(), aad=aad)
    created_at = datetime.now(timezone.utc).isoformat()
    return await db.insert_template(sess.user_id, name, t_nonce, t_ct, b_nonce, b_ct, created_at)


async def list_templates(sess: SessionKeys) -> List[Tuple[int, str]]:
    """Return list of (template_id, name)."""
    rows = await db.list_templates(sess.user_id)
    return [(r["id"], r["name"]) for r in rows]


async def get_template(sess: SessionKeys, template_id: int) -> Tuple[str, str, str]:
    """Return (name, decrypted_title, decrypted_body) for a template."""
    row = await db.get_template(template_id, sess.user_id)
    if not row:
        raise ValueError("Template not found")
    aad = sess.username.encode()
    title = aesgcm_decrypt(sess.enc_key, row["title_nonce"], row["title_ct"], aad=aad).decode()
    body = aesgcm_decrypt(sess.enc_key, row["body_nonce"], row["body_ct"], aad=aad).decode()
    return row["name"], title, body


async def delete_template(sess: SessionKeys, template_id: int) -> None:
    """Delete a template."""
    await db.delete_template(template_id, sess.user_id)


# ---------------------------------------------------------------------
# Calendar (Phase 2.7)
# ---------------------------------------------------------------------

async def get_calendar_data(sess: SessionKeys, year: int, month: int) -> Dict[str, int]:
    """Return {date_str: entry_count} for a given month."""
    rows = await db.get_entry_dates_for_month(sess.user_id, year, month)
    return {r[0]: r[1] for r in rows}


# ---------------------------------------------------------------------
# Export/Import (Phase 2.8)
# ---------------------------------------------------------------------

async def export_entries(sess: SessionKeys, fmt: str = "json") -> str:
    """Export all entries as JSON or Markdown string."""
    entries = []
    rows = await db.list_entry_rows_for_user(sess.user_id)
    aad = sess.username.encode()
    for r in rows:
        title = aesgcm_decrypt(sess.enc_key, r["title_nonce"], r["title_ct"], aad=aad).decode()
        body = aesgcm_decrypt(sess.enc_key, r["body_nonce"], r["body_ct"], aad=aad).decode()
        entries.append({
            "created_at": r["created_at"],
            "title": title,
            "body": body,
        })

    if fmt == "markdown":
        parts = []
        for e in entries:
            parts.append(f"# {e['title']}\n\n*{e['created_at']}*\n\n{e['body']}\n\n---\n")
        return "\n".join(parts)
    else:
        return json.dumps(entries, indent=2, ensure_ascii=False)


async def import_entries(sess: SessionKeys, data: str) -> int:
    """Import entries from JSON string. Returns number imported."""
    entries = json.loads(data)
    if not isinstance(entries, list):
        raise ValueError("Expected a JSON array of entries")
    count = 0
    for e in entries:
        if not isinstance(e, dict):
            continue
        title = e.get("title", "")
        body = e.get("body", "")
        if title or body:
            await add_entry(sess, title, body)
            count += 1
    return count


# ---------------------------------------------------------------------
# Drafts (Phase 2.10)
# ---------------------------------------------------------------------

async def save_draft(
    sess: SessionKeys, title: str, body: str, entry_id: int | None = None
) -> int:
    """Save or update a draft."""
    aad = sess.username.encode()
    t_nonce, t_ct = aesgcm_encrypt(sess.enc_key, title.encode(), aad=aad)
    b_nonce, b_ct = aesgcm_encrypt(sess.enc_key, body.encode(), aad=aad)
    saved_at = datetime.now(timezone.utc).isoformat()
    return await db.upsert_draft(sess.user_id, entry_id, t_nonce, t_ct, b_nonce, b_ct, saved_at)


async def get_draft(sess: SessionKeys, entry_id: int | None = None) -> Tuple[str, str] | None:
    """Return (title, body) for a draft, or None if no draft exists."""
    row = await db.get_draft(sess.user_id, entry_id)
    if not row:
        return None
    aad = sess.username.encode()
    title = aesgcm_decrypt(sess.enc_key, row["title_nonce"], row["title_ct"], aad=aad).decode()
    body = aesgcm_decrypt(sess.enc_key, row["body_nonce"], row["body_ct"], aad=aad).decode()
    return title, body


async def delete_draft(draft_id: int) -> None:
    """Delete a draft."""
    await db.delete_draft(draft_id)
