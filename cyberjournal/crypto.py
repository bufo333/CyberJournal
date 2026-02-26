# -*- coding: utf-8 -*-
"""Crypto helpers and key handling for CyberJournal.

This module encapsulates *stateless* cryptographic helpers and the
session key container. It does **not** perform any database I/O.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple
import re
import secrets

from argon2 import PasswordHasher
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes, hmac
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# ---------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------

PH = PasswordHasher(
    time_cost=2,
    memory_cost=102_400,
    parallelism=8,
    hash_len=32,
    salt_len=16,
)

SCRYPT_N = 2 ** 14
SCRYPT_R = 8
SCRYPT_P = 1

KEK_LEN = 32
DEK_LEN = 32
NONCE_LEN = 12

HKDF_INFO_ENC = b"cyberjournal/enc-key"
HKDF_INFO_HMAC = b"cyberjournal/search-key"

TOKEN_SPLIT_RE = re.compile(r"[\W_]+", re.UNICODE)


# ---------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------

@dataclass
class SessionKeys:
    """Decrypted/derived keys bound to an authenticated user session."""

    user_id: int
    username: str
    dek: bytes
    enc_key: bytes
    search_key: bytes


# ---------------------------------------------------------------------
# KDF / HKDF / AEAD helpers
# ---------------------------------------------------------------------

def scrypt_kdf(password: str, salt: bytes, length: int = KEK_LEN) -> bytes:
    """Derive a key from a password using scrypt."""
    kdf = Scrypt(salt=salt, length=length, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    return kdf.derive(password.encode("utf-8"))

def hkdf_derive(key_material: bytes, info: bytes, length: int = 32) -> bytes:
    """Derive a subkey from key material using HKDF-SHA256."""
    hk = HKDF(algorithm=hashes.SHA256(), length=length, salt=None, info=info)
    return hk.derive(key_material)

def aesgcm_encrypt(key: bytes, plaintext: bytes, aad: Optional[bytes] = None) -> Tuple[bytes, bytes]:
    """Encrypt *plaintext* with AES-GCM; return (nonce, ciphertext)."""
    nonce = secrets.token_bytes(NONCE_LEN)
    ct = AESGCM(key).encrypt(nonce, plaintext, aad)
    return nonce, ct

def aesgcm_decrypt(key: bytes, nonce: bytes, ciphertext: bytes, aad: Optional[bytes] = None) -> bytes:
    """Decrypt AES-GCM *ciphertext* with *nonce*; return plaintext."""
    return AESGCM(key).decrypt(nonce, ciphertext, aad)


# ---------------------------------------------------------------------
# Search tokenization
# ---------------------------------------------------------------------

def normalize_tokens(text: str) -> List[str]:
    """Lowercase + split on non-word characters; drop empties."""
    return [p for p in TOKEN_SPLIT_RE.split(text.lower()) if p]

def hmac_token(search_key: bytes, token: str) -> bytes:
    """HMAC(SHA256) a token with the session's search key."""
    h = hmac.HMAC(search_key, hashes.SHA256())
    h.update(token.encode("utf-8"))
    return h.finalize()
