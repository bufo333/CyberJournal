# -*- coding: utf-8 -*-
"""Shared test fixtures for CyberJournal."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# Override DB_PATH before any imports of cyberjournal modules
_tmp_dir = tempfile.mkdtemp()
os.environ["CYBERJOURNAL_DB"] = str(Path(_tmp_dir) / "test.sqlite3")


from cyberjournal.crypto import SessionKeys, hkdf_derive, HKDF_INFO_ENC, HKDF_INFO_HMAC
from cyberjournal import db
from cyberjournal import logic


@pytest.fixture(autouse=True)
async def fresh_db(tmp_path):
    """Use a fresh database for each test."""
    db_path = str(tmp_path / "test.sqlite3")
    db.DB_PATH = db_path
    await db.init_db()
    yield db_path


@pytest.fixture
async def test_user(fresh_db):
    """Register a test user and return session keys."""
    await logic.register_user("testuser", "testpass123", "favorite color?", "blue")
    sess = await logic.login_user("testuser", "testpass123")
    return sess


@pytest.fixture
def mock_session():
    """Create a mock session with known keys for unit testing crypto."""
    import secrets
    dek = secrets.token_bytes(32)
    enc_key = hkdf_derive(dek, HKDF_INFO_ENC, 32)
    search_key = hkdf_derive(dek, HKDF_INFO_HMAC, 32)
    return SessionKeys(
        user_id=1,
        username="testuser",
        dek=dek,
        enc_key=enc_key,
        search_key=search_key,
    )
