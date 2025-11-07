# =====================
# File: cyberjournal/crypto.py
# =====================
"""Cryptographic primitives and helpers for Cyberjournal.

Design goals
------------
- Password hashing: Argon2 (via argon2-cffi) for authentication.
- Key hierarchy:
    * DEK (per user, 32 bytes) randomly generated at registration.
    * KEK derived from password using scrypt; wraps DEK with AES-GCM.
    * enc_key := HKDF(DEK, info="cyberjournal/enc-key") for field encryption.
    * search_key := HKDF(DEK, info="cyberjournal/search-key") for blind index.
- Content encryption: AES-GCM with per-record 96-bit nonces.
- Blind index: normalized tokens → HMAC-SHA256(search_key, token).

Security notes
--------------
This module demonstrates practical patterns for app-level encryption. In a
production system consider SQLCipher for page-level encryption in addition
to row-level AES-GCM; add rate limiting and secure secret lifecycle.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import re
import secrets

from argon2 import PasswordHasher
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes, hmac
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# === Tunables ===
PH = PasswordHasher(time_cost=2, memory_cost=102400, parallelism=8, hash_len=32, salt_len=16)
SCRYPT_N = 2 ** 14  # via cryptography's Scrypt params
SCRYPT_R = 8
SCRYPT_P = 1
NONCE_LEN = 12  # 96-bit nonce for AES-GCM

HKDF_INFO_ENC = b"cyberjournal/enc-key"
HKDF_INFO_HMAC = b"cyberjournal/search-key"

TOKEN_SPLIT_RE = re.compile(r"[\W_]+", re.UNICODE)


def scrypt_kdf(password: str, salt: bytes, length: int = 32) -> bytes:
    """Derive a KEK from a password and salt using scrypt."""
    kdf = Scrypt(salt=salt, length=length, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    return kdf.derive(password.encode("utf-8"))


def hkdf_derive(key_material: bytes, info: bytes, length: int = 32) -> bytes:
    """Derive subkeys using HKDF-SHA256 with no salt (domain-separated via *info*)."""
    hk = HKDF(algorithm=hashes.SHA256(), length=length, salt=None, info=info)
    return hk.derive(key_material)


def aesgcm_encrypt(key: bytes, plaintext: bytes, aad: Optional[bytes] = None) -> tuple[bytes, bytes]:
    """Encrypt *plaintext* with AES-GCM; returns (nonce, ciphertext)."""
    nonce = secrets.token_bytes(NONCE_LEN)
    ct = AESGCM(key).encrypt(nonce, plaintext, aad)
    return nonce, ct


def aesgcm_decrypt(key: bytes, nonce: bytes, ciphertext: bytes, aad: Optional[bytes] = None) -> bytes:
    """Decrypt AES-GCM ciphertext and return plaintext."""
    return AESGCM(key).decrypt(nonce, ciphertext, aad)


def normalize_tokens(text: str) -> list[str]:
    """Lowercase and split on non-word boundaries; drop empties."""
    text = text.lower()
    return [p for p in TOKEN_SPLIT_RE.split(text) if p]


def hmac_token(search_key: bytes, token: str) -> bytes:
    """Compute deterministic HMAC-SHA256 over a token for blind-index equality search."""
    h = hmac.HMAC(search_key, hashes.SHA256())
    h.update(token.encode("utf-8"))
    return h.finalize()



