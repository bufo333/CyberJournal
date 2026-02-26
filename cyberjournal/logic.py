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

from . import db
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
    created_at = datetime.utcnow().isoformat()
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

    rows = await db.list_entry_rows_for_user(sess.user_id)
    for entry in rows:
        title = aesgcm_decrypt(
            sess.enc_key,
            entry["title_nonce"],
            entry["title_ct"],
            aad=sess.username.encode(),
        ).decode()
        body = aesgcm_decrypt(
            sess.enc_key,
            entry["body_nonce"],
            entry["body_ct"],
            aad=sess.username.encode(),
        ).decode()

        map_nonce = None
        map_ct = None
        map_format = entry["map_format"] or "ascii"
        if entry["map_ct"]:
            map_text = aesgcm_decrypt(
                sess.enc_key,
                entry["map_nonce"],
                entry["map_ct"],
                aad=sess.username.encode(),
            ).decode()
            map_nonce, map_ct = aesgcm_encrypt(
                new_enc_key,
                map_text.encode("utf-8"),
                aad=sess.username.encode(),
            )

        t_nonce, t_ct = aesgcm_encrypt(new_enc_key, title.encode(), aad=sess.username.encode())
        b_nonce, b_ct = aesgcm_encrypt(new_enc_key, body.encode(), aad=sess.username.encode())
        await db.update_entry_row_with_map(
            entry["id"],
            sess.user_id,
            t_nonce,
            t_ct,
            b_nonce,
            b_ct,
            map_nonce,
            map_ct,
            map_format,
        )

        await db.clear_entry_terms(entry["id"])
        terms: Set[str] = set(normalize_tokens(title) + normalize_tokens(body))
        pairs = [(entry["id"], hmac_token(new_search_key, t)) for t in terms]
        await db.insert_entry_terms(pairs)

    await db.update_user_credentials(
        sess.user_id,
        new_pwd_hash,
        new_kek_salt,
        new_wrapped,
        new_nonce,
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

async def add_entry(sess: SessionKeys, title: str, body: str) -> int:
    """Insert an encrypted entry; return new entry id."""
    created_at = datetime.utcnow().isoformat()

    # Encrypt title/body (existing behavior)
    t_nonce, t_ct = aesgcm_encrypt(sess.enc_key, title.encode(), aad=sess.username.encode())
    b_nonce, b_ct = aesgcm_encrypt(sess.enc_key, body.encode(), aad=sess.username.encode())

    # --- NEW: generate small map text and encrypt ---
    map_text, map_fmt = _render_entry_map_text(title, body, fmt="ascii", max_side=32)
    m_nonce, m_ct = aesgcm_encrypt(sess.enc_key, map_text.encode("utf-8"), aad=sess.username.encode())

    # Write the row (your db.py already supports map_* columns)
    eid = await db.insert_entry_row(
        sess.user_id,
        created_at,
        t_nonce, t_ct,
        b_nonce, b_ct,
        m_nonce, m_ct, map_fmt,
    )

    # Blind index (existing behavior)
    terms: Set[str] = set(normalize_tokens(title) + normalize_tokens(body))
    pairs = [(eid, hmac_token(sess.search_key, t)) for t in terms]
    await db.insert_entry_terms(pairs)

    return eid


async def update_entry(sess: SessionKeys, entry_id: int,
                     new_title: str, new_body: str) -> None:
    # re-encrypt fields with per-field fresh nonces (AES-GCM)
    t_nonce, t_ct = aesgcm_encrypt(sess.enc_key, new_title.encode(), aad=sess.username.encode())
    b_nonce, b_ct = aesgcm_encrypt(sess.enc_key, new_body.encode(),  aad=sess.username.encode())

    # update row
    await db.update_entry_row(entry_id, sess.user_id, t_nonce, t_ct, b_nonce, b_ct)

    # rebuild blind index terms
    await db.clear_entry_terms(entry_id)
    terms: Set[str] = set(normalize_tokens(new_title) + normalize_tokens(new_body))
    pairs: list[Tuple[int, bytes]] = [(entry_id, hmac_token(sess.search_key, t)) for t in terms]
    if pairs:
        await db.insert_entry_terms(pairs)


async def delete_entry(sess: SessionKeys, entry_id: int) -> None:
    await db.delete_entry_row(entry_id, sess.user_id)


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
        raise ValueError("Entry not found")
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
        raise ValueError("Entry not found")

    # decrypt title / body (unchanged)
    title = aesgcm_decrypt(sess.enc_key, row["title_nonce"], row["title_ct"], aad=sess.username.encode()).decode()
    body  = aesgcm_decrypt(sess.enc_key, row["body_nonce"],  row["body_ct"],  aad=sess.username.encode()).decode()

    # decrypt map if present
    map_text = ""
    map_fmt  = (row["map_format"] or "ascii") if "map_format" in row.keys() else "ascii"
    if "map_ct" in row.keys() and row["map_ct"]:
        map_text = aesgcm_decrypt(sess.enc_key, row["map_nonce"], row["map_ct"], aad=sess.username.encode()).decode()

    return row["created_at"], title, body, map_text, map_fmt
