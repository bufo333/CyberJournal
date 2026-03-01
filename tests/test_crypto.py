# -*- coding: utf-8 -*-
"""Tests for cyberjournal.crypto module."""
from __future__ import annotations

import pytest
from cyberjournal.crypto import (
    scrypt_kdf,
    hkdf_derive,
    aesgcm_encrypt,
    aesgcm_decrypt,
    normalize_tokens,
    hmac_token,
    PH,
)


class TestScryptKdf:
    def test_deterministic(self):
        salt = b"0123456789abcdef"
        k1 = scrypt_kdf("password", salt, 32)
        k2 = scrypt_kdf("password", salt, 32)
        assert k1 == k2

    def test_different_passwords_different_keys(self):
        salt = b"0123456789abcdef"
        k1 = scrypt_kdf("pass1", salt, 32)
        k2 = scrypt_kdf("pass2", salt, 32)
        assert k1 != k2

    def test_different_salts_different_keys(self):
        k1 = scrypt_kdf("password", b"salt1___________", 32)
        k2 = scrypt_kdf("password", b"salt2___________", 32)
        assert k1 != k2

    def test_output_length(self):
        salt = b"0123456789abcdef"
        assert len(scrypt_kdf("pw", salt, 16)) == 16
        assert len(scrypt_kdf("pw", salt, 64)) == 64


class TestHkdfDerive:
    def test_deterministic(self):
        key = b"x" * 32
        k1 = hkdf_derive(key, b"info1", 32)
        k2 = hkdf_derive(key, b"info1", 32)
        assert k1 == k2

    def test_different_info_different_keys(self):
        key = b"x" * 32
        k1 = hkdf_derive(key, b"info1", 32)
        k2 = hkdf_derive(key, b"info2", 32)
        assert k1 != k2


class TestAesGcm:
    def test_round_trip(self):
        key = b"k" * 32
        plaintext = b"hello world"
        nonce, ct = aesgcm_encrypt(key, plaintext)
        assert aesgcm_decrypt(key, nonce, ct) == plaintext

    def test_round_trip_with_aad(self):
        key = b"k" * 32
        plaintext = b"secret data"
        aad = b"testuser"
        nonce, ct = aesgcm_encrypt(key, plaintext, aad=aad)
        assert aesgcm_decrypt(key, nonce, ct, aad=aad) == plaintext

    def test_wrong_key_fails(self):
        key1 = b"a" * 32
        key2 = b"b" * 32
        nonce, ct = aesgcm_encrypt(key1, b"data")
        with pytest.raises(Exception):
            aesgcm_decrypt(key2, nonce, ct)

    def test_wrong_aad_fails(self):
        key = b"k" * 32
        nonce, ct = aesgcm_encrypt(key, b"data", aad=b"user1")
        with pytest.raises(Exception):
            aesgcm_decrypt(key, nonce, ct, aad=b"user2")

    def test_unique_nonces(self):
        key = b"k" * 32
        n1, _ = aesgcm_encrypt(key, b"data")
        n2, _ = aesgcm_encrypt(key, b"data")
        assert n1 != n2


class TestTokenization:
    def test_basic(self):
        assert normalize_tokens("Hello World") == ["hello", "world"]

    def test_punctuation(self):
        assert normalize_tokens("it's a test!") == ["it", "s", "a", "test"]

    def test_empty(self):
        assert normalize_tokens("") == []
        assert normalize_tokens("   ") == []

    def test_unicode(self):
        tokens = normalize_tokens("café résumé")
        assert "café" in tokens
        assert "résumé" in tokens


class TestHmacToken:
    def test_deterministic(self):
        key = b"k" * 32
        h1 = hmac_token(key, "hello")
        h2 = hmac_token(key, "hello")
        assert h1 == h2

    def test_different_tokens(self):
        key = b"k" * 32
        h1 = hmac_token(key, "hello")
        h2 = hmac_token(key, "world")
        assert h1 != h2

    def test_different_keys(self):
        h1 = hmac_token(b"a" * 32, "hello")
        h2 = hmac_token(b"b" * 32, "hello")
        assert h1 != h2


class TestPasswordHasher:
    def test_hash_and_verify(self):
        h = PH.hash("mypassword")
        PH.verify(h, "mypassword")  # should not raise

    def test_wrong_password(self):
        from argon2.exceptions import VerifyMismatchError
        h = PH.hash("correct")
        with pytest.raises(VerifyMismatchError):
            PH.verify(h, "wrong")
