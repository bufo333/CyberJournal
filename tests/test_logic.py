# -*- coding: utf-8 -*-
"""Tests for cyberjournal.logic module."""
from __future__ import annotations

import json
import pytest
from cyberjournal import logic
from cyberjournal.errors import EntryNotFoundError


class TestAuth:
    async def test_register_and_login(self, fresh_db):
        await logic.register_user("alice", "pass123", "color?", "red")
        sess = await logic.login_user("alice", "pass123")
        assert sess.username == "alice"
        assert sess.user_id > 0

    async def test_wrong_password(self, fresh_db):
        await logic.register_user("bob", "correct", "q?", "a")
        with pytest.raises(ValueError, match="Invalid password"):
            await logic.login_user("bob", "wrong")

    async def test_nonexistent_user(self, fresh_db):
        with pytest.raises(ValueError, match="User not found"):
            await logic.login_user("nobody", "pass")

    async def test_duplicate_registration(self, fresh_db):
        await logic.register_user("charlie", "pass", "q?", "a")
        with pytest.raises(Exception):
            await logic.register_user("charlie", "pass2", "q?", "a")

    async def test_security_question(self, fresh_db):
        await logic.register_user("dave", "pass", "What pet?", "dog")
        q = await logic.get_security_question("dave")
        assert q == "What pet?"

    async def test_password_reset(self, fresh_db):
        await logic.register_user("eve", "oldpass", "num?", "42")
        sess = await logic.login_user("eve", "oldpass")
        await logic.add_entry(sess, "test", "body")

        await logic.reset_password_with_security_answer("eve", "42", "newpass")
        sess2 = await logic.login_user("eve", "newpass")
        entries = await logic.list_entries(sess2)
        assert len(entries) == 0  # entries wiped


class TestEntries:
    async def test_add_and_list(self, test_user):
        eid = await logic.add_entry(test_user, "My Title", "My body text")
        entries = await logic.list_entries(test_user)
        assert len(entries) == 1
        assert entries[0][0] == eid
        assert entries[0][2] == "My Title"

    async def test_get_entry(self, test_user):
        eid = await logic.add_entry(test_user, "Title", "Body content")
        created_at, title, body = await logic.get_entry(test_user, eid)
        assert title == "Title"
        assert body == "Body content"

    async def test_get_nonexistent_entry(self, test_user):
        with pytest.raises(EntryNotFoundError):
            await logic.get_entry(test_user, 99999)

    async def test_update_entry(self, test_user):
        eid = await logic.add_entry(test_user, "Old", "Old body")
        await logic.update_entry(test_user, eid, "New", "New body")
        _, title, body = await logic.get_entry(test_user, eid)
        assert title == "New"
        assert body == "New body"

    async def test_delete_entry(self, test_user):
        eid = await logic.add_entry(test_user, "Del", "Body")
        await logic.delete_entry(test_user, eid)
        with pytest.raises(EntryNotFoundError):
            await logic.get_entry(test_user, eid)

    async def test_entry_with_map(self, test_user):
        eid = await logic.add_entry(test_user, "Map Test", "A long body with many words for the map generator")
        created_at, title, body, map_text, map_fmt = await logic.get_entry_with_map(test_user, eid)
        assert title == "Map Test"
        assert map_text  # should have generated a map

    async def test_entry_with_mood_weather(self, test_user):
        eid = await logic.add_entry(test_user, "Mood", "Body", mood="happy", weather="sunny")
        entry = await logic.get_entry_full(test_user, eid)
        assert entry["mood"] == "happy"
        assert entry["weather"] == "sunny"


class TestSearch:
    async def test_basic_search(self, test_user):
        await logic.add_entry(test_user, "Python Programming", "Learning about decorators")
        await logic.add_entry(test_user, "Cooking Recipe", "Making pasta from scratch")

        results = await logic.search_entries(test_user, "python")
        assert len(results) == 1

    async def test_and_search(self, test_user):
        await logic.add_entry(test_user, "Python Web", "Flask and Django")
        await logic.add_entry(test_user, "Python Data", "Pandas and NumPy")

        results = await logic.search_entries(test_user, "python")
        assert len(results) == 2

        results = await logic.search_entries(test_user, "python flask")
        assert len(results) == 1

    async def test_empty_search(self, test_user):
        results = await logic.search_entries(test_user, "")
        assert results == []


class TestFavorites:
    async def test_toggle_favorite(self, test_user):
        eid = await logic.add_entry(test_user, "Fav", "Body")
        assert await logic.toggle_favorite(test_user, eid) is True
        assert await logic.toggle_favorite(test_user, eid) is False


class TestTags:
    async def test_add_and_list_tags(self, test_user):
        eid = await logic.add_entry(test_user, "Tagged", "Body")
        tag_id = await logic.add_tag(test_user, eid, "important")
        tags = await logic.list_tags(test_user, eid)
        assert len(tags) == 1
        assert tags[0][1] == "important"

    async def test_search_by_tag(self, test_user):
        eid = await logic.add_entry(test_user, "Tagged", "Body")
        await logic.add_tag(test_user, eid, "work")
        results = await logic.search_by_tag(test_user, "work")
        assert eid in results

    async def test_remove_tag(self, test_user):
        eid = await logic.add_entry(test_user, "Tagged", "Body")
        tag_id = await logic.add_tag(test_user, eid, "temp")
        await logic.remove_tag(test_user, tag_id)
        tags = await logic.list_tags(test_user, eid)
        assert len(tags) == 0


class TestNotebooks:
    async def test_create_and_list(self, test_user):
        nb_id = await logic.create_notebook(test_user, "Work")
        notebooks = await logic.list_notebooks(test_user)
        assert len(notebooks) == 1
        assert notebooks[0][1] == "Work"

    async def test_assign_entry(self, test_user):
        nb_id = await logic.create_notebook(test_user, "Personal")
        eid = await logic.add_entry(test_user, "Entry", "Body")
        await logic.assign_entry_notebook(test_user, eid, nb_id)
        entry = await logic.get_entry_full(test_user, eid)
        assert entry["notebook_id"] == nb_id

    async def test_delete_notebook(self, test_user):
        nb_id = await logic.create_notebook(test_user, "Temp")
        await logic.delete_notebook(test_user, nb_id)
        notebooks = await logic.list_notebooks(test_user)
        assert len(notebooks) == 0


class TestTemplates:
    async def test_create_and_get(self, test_user):
        tpl_id = await logic.create_template(test_user, "Daily", "Day Title", "What happened today?")
        name, title, body = await logic.get_template(test_user, tpl_id)
        assert name == "Daily"
        assert title == "Day Title"
        assert body == "What happened today?"

    async def test_list_templates(self, test_user):
        await logic.create_template(test_user, "T1", "t", "b")
        await logic.create_template(test_user, "T2", "t", "b")
        templates = await logic.list_templates(test_user)
        assert len(templates) == 2


class TestExportImport:
    async def test_json_export_import(self, test_user):
        await logic.add_entry(test_user, "Export Test", "Body for export")
        data = await logic.export_entries(test_user, "json")
        parsed = json.loads(data)
        assert len(parsed) == 1
        assert parsed[0]["title"] == "Export Test"

        # Import into same user
        count = await logic.import_entries(test_user, data)
        assert count == 1
        entries = await logic.list_entries(test_user)
        assert len(entries) == 2

    async def test_markdown_export(self, test_user):
        await logic.add_entry(test_user, "MD Test", "Markdown body")
        data = await logic.export_entries(test_user, "markdown")
        assert "# MD Test" in data


class TestCalendar:
    async def test_calendar_data(self, test_user):
        await logic.add_entry(test_user, "Cal", "Body")
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        data = await logic.get_calendar_data(test_user, now.year, now.month)
        assert len(data) >= 1


class TestDrafts:
    async def test_save_and_get_draft(self, test_user):
        await logic.save_draft(test_user, "Draft Title", "Draft Body")
        result = await logic.get_draft(test_user)
        assert result is not None
        assert result[0] == "Draft Title"
        assert result[1] == "Draft Body"

    async def test_no_draft(self, test_user):
        result = await logic.get_draft(test_user)
        assert result is None


class TestPagination:
    async def test_paginated_list(self, test_user):
        for i in range(25):
            await logic.add_entry(test_user, f"Entry {i}", f"Body {i}")

        total = await logic.count_entries(test_user)
        assert total == 25

        page1 = await logic.list_entries_paginated(test_user, limit=10, offset=0)
        assert len(page1) == 10

        page3 = await logic.list_entries_paginated(test_user, limit=10, offset=20)
        assert len(page3) == 5
