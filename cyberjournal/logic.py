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
import secrets

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
# Auth and key management
# ---------------------------------------------------------------------

async def register_user(username: str, password: str) -> None:
    """Register a new user and store a wrapped DEK in the DB."""
    created_at = datetime.utcnow().isoformat()
    pwd_hash = PH.hash(password)

    dek = secrets.token_bytes(DEK_LEN)
    kek_salt = secrets.token_bytes(16)
    kek = scrypt_kdf(password, kek_salt, 32)
    wrap_key = hkdf_derive(kek, b"wrap-key", 32)
    nonce, wrapped = aesgcm_encrypt(wrap_key, dek, aad=username.encode())

    await db.insert_user(username, pwd_hash, kek_salt, wrapped, nonce, created_at)

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


# ---------------------------------------------------------------------
# Entries (encrypted title/body) + blind index
# ---------------------------------------------------------------------

async def add_entry(sess: SessionKeys, title: str, body: str) -> int:
    """Insert an encrypted entry; return new entry id."""
    created_at = datetime.now(timezone.utc).isoformat()
    t_nonce, t_ct = aesgcm_encrypt(sess.enc_key, title.encode(), aad=sess.username.encode())
    b_nonce, b_ct = aesgcm_encrypt(sess.enc_key, body.encode(), aad=sess.username.encode())
    eid = await db.insert_entry_row(sess.user_id, created_at, t_nonce, t_ct, b_nonce, b_ct)

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
