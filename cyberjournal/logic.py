# =====================
# File: cyberjournal/logic.py
# =====================
"""Business logic for registration, login, and encrypted entries.

This module composes the :mod:`cyberjournal.crypto` primitives with the SQL
layer in :mod:`cyberjournal.db`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from argon2.exceptions import VerifyMismatchError

from . import db
from .crypto import (
    PH,
    scrypt_kdf,
    hkdf_derive,
    aesgcm_encrypt,
    aesgcm_decrypt,
    normalize_tokens,
    hmac_token,
)


@dataclass(slots=True)
class SessionKeys:
    """In-memory session key material for an authenticated user."""
    user_id: int
    username: str
    dek: bytes
    enc_key: bytes
    search_key: bytes


async def initialize() -> None:
    """Initialize persistent storage."""
    await db.init_db()


# --- Auth lifecycle ---
import secrets


async def register_user(username: str, password: str) -> None:
    """Create a new user and store a wrapped DEK.

    - Hash password with Argon2 for login verification.
    - Derive KEK from password via scrypt (random salt per user).
    - Wrap randomly generated DEK using AES-GCM with HKDF-dervived wrap key.
    """
    created_at = datetime.utcnow().isoformat()
    pwd_hash = PH.hash(password)

    dek = secrets.token_bytes(32)
    kek_salt = secrets.token_bytes(16)
    kek = scrypt_kdf(password, kek_salt, 32)
    wrap_key = hkdf_derive(kek, b"wrap-key", 32)
    nonce, wrapped = aesgcm_encrypt(wrap_key, dek, aad=username.encode())

    await db.insert_user(username, pwd_hash, kek_salt, wrapped, nonce, created_at)


async def login_user(username: str, password: str) -> SessionKeys:
    """Verify credentials, unwrap the DEK, and derive session subkeys."""
    row = await db.user_by_username(username)
    if not row:
        raise ValueError("User not found")

    try:
        PH.verify(row["pwd_hash"], password)
    except VerifyMismatchError as exc:
        raise ValueError("Invalid password") from exc

    kek = scrypt_kdf(password, row["kek_salt"], 32)
    wrap_key = hkdf_derive(kek, b"wrap-key", 32)
    dek = aesgcm_decrypt(wrap_key, row["dek_wrap_nonce"], row["dek_wrapped"], aad=username.encode())

    enc_key = hkdf_derive(dek, b"cyberjournal/enc-key", 32)
    search_key = hkdf_derive(dek, b"cyberjournal/search-key", 32)
    return SessionKeys(user_id=int(row["id"]), username=username, dek=dek, enc_key=enc_key, search_key=search_key)


# --- Entry lifecycle ---


async def add_entry(sess: SessionKeys, title: str, body: str) -> int:
    """Encrypt and insert a journal entry; returns the entry id."""
    created_at = datetime.utcnow().isoformat()
    title_nonce, title_ct = aesgcm_encrypt(sess.enc_key, title.encode(), aad=sess.username.encode())
    body_nonce, body_ct = aesgcm_encrypt(sess.enc_key, body.encode(), aad=sess.username.encode())
    entry_id = await db.insert_entry(sess.user_id, created_at, title_nonce, title_ct, body_nonce, body_ct)

    # Blind index terms
    terms = set(normalize_tokens(title) + normalize_tokens(body))
    term_hashes = [hmac_token(sess.search_key, t) for t in terms]
    await db.insert_terms(entry_id, term_hashes)
    return entry_id


async def list_entries(sess: SessionKeys) -> list[tuple[int, str, str]]:
    """Return [(entry_id, created_at, decrypted_title), ...] for user."""
    rows = await db.fetch_entries_for_user(sess.user_id)
    out: list[tuple[int, str, str]] = []
    for r in rows:
        title = aesgcm_decrypt(sess.enc_key, r["title_nonce"], r["title_ct"], aad=sess.username.encode()).decode()
        out.append((int(r["id"]), str(r["created_at"]), title))
    return out


async def get_entry(sess: SessionKeys, entry_id: int) -> tuple[str, str, str]:
    """Return (created_at, title, body) for a single entry."""
    row = await db.fetch_entry(sess.user_id, entry_id)
    if not row:
        raise ValueError("Entry not found")
    title = aesgcm_decrypt(sess.enc_key, row["title_nonce"], row["title_ct"], aad=sess.username.encode()).decode()
    body = aesgcm_decrypt(sess.enc_key, row["body_nonce"], row["body_ct"], aad=sess.username.encode()).decode()
    return str(row["created_at"]), title, body


async def search_entries(sess: SessionKeys, query: str) -> list[int]:
    """Return entry ids containing **all** tokens in *query* (AND semantics)."""
    tokens = [t for t in normalize_tokens(query) if t]
    if not tokens:
        return []
    id_sets: list[set[int]] = []
    for token in tokens:
        th = hmac_token(sess.search_key, token)
        ids = await db.fetch_entry_ids_for_term_hash(th)
        id_sets.append(ids)
    if not id_sets:
        return []
    return sorted(set.intersection(*id_sets), reverse=True)
