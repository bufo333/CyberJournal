# -*- coding: utf-8 -*-
"""Tests for cyberjournal.db module."""
from __future__ import annotations

import pytest
from cyberjournal import db
from cyberjournal.errors import DuplicateUserError


class TestUsers:
    async def test_insert_and_get_user(self, fresh_db):
        await db.insert_user(
            "alice", "hash", b"salt" * 4, b"wrapped" * 4, b"nonce" * 4,
            "question", "answer_hash", "2024-01-01T00:00:00",
        )
        row = await db.get_user_by_username("alice")
        assert row is not None
        assert row["username"] == "alice"

    async def test_get_nonexistent_user(self, fresh_db):
        row = await db.get_user_by_username("nobody")
        assert row is None

    async def test_duplicate_username(self, fresh_db):
        await db.insert_user(
            "alice", "hash", b"salt" * 4, b"w" * 4, b"n" * 4,
            "q", "a", "2024-01-01T00:00:00",
        )
        with pytest.raises(DuplicateUserError):
            await db.insert_user(
                "alice", "hash2", b"salt" * 4, b"w" * 4, b"n" * 4,
                "q", "a", "2024-01-01T00:00:00",
            )

    async def test_update_credentials(self, fresh_db):
        await db.insert_user(
            "bob", "hash1", b"s" * 16, b"w" * 16, b"n" * 12,
            "q", "a", "2024-01-01T00:00:00",
        )
        row = await db.get_user_by_username("bob")
        await db.update_user_credentials(row["id"], "hash2", b"s2" * 8, b"w2" * 8, b"n2" * 6)
        row2 = await db.get_user_by_username("bob")
        assert row2["pwd_hash"] == "hash2"

    async def test_security_question(self, fresh_db):
        await db.insert_user(
            "charlie", "h", b"s" * 16, b"w" * 16, b"n" * 12,
            "What color?", "blue_hash", "2024-01-01T00:00:00",
        )
        row = await db.get_user_security_question("charlie")
        assert row["security_question"] == "What color?"


class TestEntries:
    async def test_insert_and_get(self, fresh_db):
        await db.insert_user(
            "user1", "h", b"s" * 16, b"w" * 16, b"n" * 12,
            "q", "a", "2024-01-01T00:00:00",
        )
        row = await db.get_user_by_username("user1")
        uid = row["id"]

        eid = await db.insert_entry_row(
            uid, "2024-06-15T10:00:00",
            b"tn" * 6, b"tc" * 10,
            b"bn" * 6, b"bc" * 10,
        )
        assert eid is not None

        entry = await db.get_entry_row(uid, eid)
        assert entry is not None
        assert entry["created_at"] == "2024-06-15T10:00:00"

    async def test_list_headers(self, fresh_db):
        await db.insert_user(
            "user2", "h", b"s" * 16, b"w" * 16, b"n" * 12,
            "q", "a", "2024-01-01T00:00:00",
        )
        row = await db.get_user_by_username("user2")
        uid = row["id"]

        await db.insert_entry_row(uid, "2024-01-01", b"n" * 12, b"c" * 10, b"n" * 12, b"c" * 10)
        await db.insert_entry_row(uid, "2024-01-02", b"n" * 12, b"c" * 10, b"n" * 12, b"c" * 10)

        headers = await db.list_entry_headers(uid)
        assert len(headers) == 2

    async def test_delete_entry(self, fresh_db):
        await db.insert_user(
            "user3", "h", b"s" * 16, b"w" * 16, b"n" * 12,
            "q", "a", "2024-01-01T00:00:00",
        )
        row = await db.get_user_by_username("user3")
        uid = row["id"]

        eid = await db.insert_entry_row(uid, "2024-01-01", b"n" * 12, b"c" * 10, b"n" * 12, b"c" * 10)
        await db.delete_entry_row(eid, uid)
        assert await db.get_entry_row(uid, eid) is None

    async def test_favorite_toggle(self, fresh_db):
        await db.insert_user(
            "user4", "h", b"s" * 16, b"w" * 16, b"n" * 12,
            "q", "a", "2024-01-01T00:00:00",
        )
        row = await db.get_user_by_username("user4")
        uid = row["id"]

        eid = await db.insert_entry_row(uid, "2024-01-01", b"n" * 12, b"c" * 10, b"n" * 12, b"c" * 10)
        result = await db.toggle_favorite(eid, uid)
        assert result is True
        result2 = await db.toggle_favorite(eid, uid)
        assert result2 is False


class TestEntryTerms:
    async def test_insert_and_search(self, fresh_db):
        await db.insert_user(
            "user5", "h", b"s" * 16, b"w" * 16, b"n" * 12,
            "q", "a", "2024-01-01T00:00:00",
        )
        row = await db.get_user_by_username("user5")
        uid = row["id"]

        eid = await db.insert_entry_row(uid, "2024-01-01", b"n" * 12, b"c" * 10, b"n" * 12, b"c" * 10)
        await db.insert_entry_terms([(eid, b"term_hash_1"), (eid, b"term_hash_2")])

        ids = await db.get_entry_ids_for_term(b"term_hash_1")
        assert eid in ids

    async def test_clear_terms(self, fresh_db):
        await db.insert_user(
            "user6", "h", b"s" * 16, b"w" * 16, b"n" * 12,
            "q", "a", "2024-01-01T00:00:00",
        )
        row = await db.get_user_by_username("user6")
        uid = row["id"]

        eid = await db.insert_entry_row(uid, "2024-01-01", b"n" * 12, b"c" * 10, b"n" * 12, b"c" * 10)
        await db.insert_entry_terms([(eid, b"hash1")])
        await db.clear_entry_terms(eid)
        ids = await db.get_entry_ids_for_term(b"hash1")
        assert eid not in ids


class TestTags:
    async def test_tag_crud(self, fresh_db):
        await db.insert_user(
            "user7", "h", b"s" * 16, b"w" * 16, b"n" * 12,
            "q", "a", "2024-01-01T00:00:00",
        )
        row = await db.get_user_by_username("user7")
        uid = row["id"]

        eid = await db.insert_entry_row(uid, "2024-01-01", b"n" * 12, b"c" * 10, b"n" * 12, b"c" * 10)
        tag_id = await db.insert_entry_tag(eid, b"tn" * 6, b"tc" * 10, b"hash" * 8)

        tags = await db.get_tags_for_entry(eid)
        assert len(tags) == 1

        ids = await db.get_entry_ids_for_tag_hash(b"hash" * 8)
        assert eid in ids

        await db.delete_entry_tag(tag_id)
        tags2 = await db.get_tags_for_entry(eid)
        assert len(tags2) == 0


class TestNotebooks:
    async def test_notebook_crud(self, fresh_db):
        await db.insert_user(
            "user8", "h", b"s" * 16, b"w" * 16, b"n" * 12,
            "q", "a", "2024-01-01T00:00:00",
        )
        row = await db.get_user_by_username("user8")
        uid = row["id"]

        nb_id = await db.insert_notebook(uid, b"n" * 12, b"c" * 10, "2024-01-01T00:00:00")
        notebooks = await db.list_notebooks(uid)
        assert len(notebooks) == 1

        await db.delete_notebook(nb_id, uid)
        notebooks2 = await db.list_notebooks(uid)
        assert len(notebooks2) == 0


class TestAtomicPasswordChange:
    async def test_atomic_change(self, fresh_db):
        await db.insert_user(
            "user9", "hash1", b"s" * 16, b"w" * 16, b"n" * 12,
            "q", "a", "2024-01-01T00:00:00",
        )
        row = await db.get_user_by_username("user9")
        uid = row["id"]

        eid = await db.insert_entry_row(uid, "2024-01-01", b"n" * 12, b"c" * 10, b"n" * 12, b"c" * 10)

        await db.change_password_atomically(
            uid,
            "hash2", b"s2" * 8, b"w2" * 8, b"n2" * 6,
            entry_updates=[{
                "id": eid,
                "title_nonce": b"tn" * 6, "title_ct": b"tc" * 10,
                "body_nonce": b"bn" * 6, "body_ct": b"bc" * 10,
                "map_nonce": None, "map_ct": None, "map_format": "ascii",
            }],
            term_updates=[(eid, [(eid, b"new_hash")])],
        )

        row2 = await db.get_user_by_username("user9")
        assert row2["pwd_hash"] == "hash2"
