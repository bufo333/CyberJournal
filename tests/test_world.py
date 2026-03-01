# -*- coding: utf-8 -*-
"""Tests for cyberjournal.world module."""
from __future__ import annotations

import os
import tempfile

import pytest

from cyberjournal.world import world_db
from cyberjournal.world.biomes import classify_biome, derive_entity_type
from cyberjournal.world.grid import (
    _keyword_set,
    _similarity,
    find_best_placement,
    generate_chunk,
    CHUNK_W,
    CHUNK_H,
)
from cyberjournal.world.timeline import get_era, advance_turn
from cyberjournal.world.renderer import render_world_viewport, render_tile_info
from cyberjournal.world.events import trigger_events, MOOD_EVENTS, KEYWORD_EVENTS
from cyberjournal.world.weather import mood_to_weather, propagate_weather, apply_weather_to_world, get_weather_at
from cyberjournal.world.economy import get_biome_resources, find_path, RESOURCE_VALUES
from cyberjournal.world.npcs import extract_proper_nouns, generate_npc_name, generate_npcs
from cyberjournal.world.quests import generate_quests, get_active_quests, complete_quest_at


@pytest.fixture(autouse=True)
async def fresh_world_db(tmp_path):
    """Use a fresh world database for each test."""
    db_path = str(tmp_path / "test_world.sqlite3")
    world_db.WORLD_DB_PATH = db_path
    await world_db.init_world_db()
    yield db_path


class TestWorldDb:
    async def test_meta(self, fresh_world_db):
        await world_db.set_meta("test_key", "test_value")
        assert await world_db.get_meta("test_key") == "test_value"
        assert await world_db.get_meta("nonexistent") is None

    async def test_tiles(self, fresh_world_db):
        await world_db.set_tiles_batch([{
            "x": 0, "y": 0, "terrain": "field", "elevation": 0.5,
            "moisture": 0.5, "biome": "grassland", "entry_id": 1,
            "chunk_x": 0, "chunk_y": 0,
        }])
        tile = await world_db.get_tile(0, 0)
        assert tile is not None
        assert tile["terrain"] == "field"

    async def test_tiles_in_rect(self, fresh_world_db):
        tiles = [
            {"x": i, "y": j, "terrain": "field", "elevation": 0.5,
             "moisture": 0.5, "biome": "grassland", "entry_id": 1,
             "chunk_x": 0, "chunk_y": 0}
            for i in range(5) for j in range(5)
        ]
        await world_db.set_tiles_batch(tiles)
        result = await world_db.get_tiles_in_rect(0, 0, 2, 2)
        assert len(result) == 9

    async def test_entities(self, fresh_world_db):
        eid = await world_db.insert_entity(5, 5, "settlement", "Test Town")
        entities = await world_db.get_entities_in_rect(0, 0, 10, 10)
        assert len(entities) == 1
        assert entities[0]["name"] == "Test Town"

    async def test_clear_entries(self, fresh_world_db):
        await world_db.set_tiles_batch([{
            "x": 0, "y": 0, "terrain": "field", "elevation": 0.5,
            "moisture": 0.5, "biome": "grassland", "entry_id": 42,
            "chunk_x": 0, "chunk_y": 0,
        }])
        await world_db.clear_tiles_for_entry(42)
        assert await world_db.get_tile(0, 0) is None

    async def test_history(self, fresh_world_db):
        await world_db.insert_history_event(
            1, "test_event", "Something happened", created_at="2024-01-01"
        )
        history = await world_db.get_history()
        assert len(history) == 1
        assert history[0]["event_type"] == "test_event"


class TestBiomes:
    def test_ocean(self):
        assert classify_biome("water", 0.2, 0.5) == "ocean"

    def test_grassland(self):
        assert classify_biome("field", 0.5, 0.55) == "grassland"

    def test_rainforest(self):
        assert classify_biome("forest", 0.5, 0.8) == "rainforest"

    def test_alpine(self):
        assert classify_biome("mount", 0.9, 0.6) == "alpine"

    def test_derive_settlement(self):
        assert derive_entity_type(300, "happy", "grassland") == "settlement"

    def test_derive_ruin(self):
        assert derive_entity_type(50, "sad", "grassland") == "ruin"

    def test_derive_shrine(self):
        assert derive_entity_type(50, "calm", "grassland") == "shrine"


class TestGrid:
    def test_keyword_set(self):
        kws = _keyword_set("Python Programming", "Learning about decorators and generators")
        assert "python" in kws
        assert "programming" in kws
        assert "decorators" in kws

    def test_similarity(self):
        s1 = {"python", "programming", "code"}
        s2 = {"python", "programming", "data"}
        sim = _similarity(s1, s2)
        assert 0.0 < sim < 1.0

    def test_similarity_identical(self):
        s = {"hello", "world"}
        assert _similarity(s, s) == 1.0

    def test_similarity_disjoint(self):
        s1 = {"alpha", "beta"}
        s2 = {"gamma", "delta"}
        assert _similarity(s1, s2) == 0.0

    async def test_find_first_placement(self, fresh_world_db):
        pos = await find_best_placement({"test"}, {}, {})
        assert pos == (0, 0)

    async def test_find_adjacent_placement(self, fresh_world_db):
        placements = {1: (0, 0)}
        kw_map = {1: {"python", "code"}}
        pos = await find_best_placement({"python", "data"}, placements, kw_map)
        assert pos != (0, 0)
        # Should be adjacent to (0, 0)
        assert abs(pos[0]) <= 1 and abs(pos[1]) <= 1

    async def test_generate_chunk(self, fresh_world_db):
        await generate_chunk(1, "Test Title", "Test body with some words", 0, 0)
        tile = await world_db.get_tile(0, 0)
        assert tile is not None
        assert tile["entry_id"] == 1

        tiles = await world_db.get_tiles_in_rect(0, 0, CHUNK_W - 1, CHUNK_H - 1)
        assert len(tiles) == CHUNK_W * CHUNK_H


class TestTimeline:
    def test_era_names(self):
        assert get_era(0) == "Primordial Era"
        assert get_era(10) == "Dawn Age"
        assert get_era(50) == "Age of Growth"
        assert get_era(200) == "Age of Prosperity"

    async def test_advance_turn(self, fresh_world_db):
        turn = await advance_turn(1, "First Entry")
        assert turn == 1
        turn2 = await advance_turn(2, "Second Entry")
        assert turn2 == 2
        assert await world_db.get_current_turn() == 2


class TestRenderer:
    async def test_render_empty(self, fresh_world_db):
        text = render_world_viewport([], [], 0, 0, 10, 5, 0, 0, color=False)
        assert len(text.split("\n")) == 5

    async def test_render_with_tiles(self, fresh_world_db):
        tiles = [
            {"x": i, "y": j, "terrain": "field", "biome": "grassland", "elevation": 0.5}
            for i in range(10) for j in range(5)
        ]
        entities = [{"x": 5, "y": 2, "type": "settlement", "name": "Town"}]
        text = render_world_viewport(tiles, entities, 0, 0, 10, 5, 5, 2, color=False)
        assert "@" in text or "H" in text  # cursor or settlement

    def test_tile_info(self):
        tile = {"terrain": "forest", "biome": "woodland", "elevation": 0.65, "entry_id": 1}
        info = render_tile_info(tile, None, 5, 5)
        assert "forest" in info
        assert "woodland" in info

    def test_tile_info_empty(self):
        info = render_tile_info(None, None, 0, 0)
        assert "Unexplored" in info

    def test_tile_info_entity(self):
        tile = {"terrain": "field", "biome": "grassland", "elevation": 0.5}
        entity = {"name": "Test Town", "type": "settlement"}
        info = render_tile_info(tile, entity, 5, 5)
        assert "Test Town" in info


class TestEvents:
    async def test_mood_events(self, fresh_world_db):
        events = await trigger_events(1, "Happy Day", "What a great day", "happy", 1, 0, 0)
        assert len(events) >= 1
        mood_types = {e["type"] for e in events}
        assert "festival" in mood_types or "harvest" in mood_types

    async def test_keyword_events(self, fresh_world_db):
        events = await trigger_events(1, "A Journey", "I went on a long travel", "neutral", 1, 0, 0)
        types = {e["type"] for e in events}
        assert "discovery" in types

    async def test_no_duplicate_event_types(self, fresh_world_db):
        events = await trigger_events(1, "Travel journey", "travel journey explore", "neutral", 1, 0, 0)
        types = [e["type"] for e in events]
        # "discovery" should appear only once even though both travel and journey map to it
        assert types.count("discovery") <= 1

    async def test_events_stored_in_history(self, fresh_world_db):
        await trigger_events(1, "Battle", "A fierce fight broke out", "anxious", 1, 0, 0)
        history = await world_db.get_history()
        assert len(history) >= 1


class TestWeather:
    def test_mood_to_weather(self):
        assert mood_to_weather("happy") == "clear"
        assert mood_to_weather("sad") == "rain"
        assert mood_to_weather("anxious") == "storm"
        assert mood_to_weather("unknown_mood") == "mild"

    def test_propagate_weather(self):
        grid = [["clear", "rain", "clear"],
                ["clear", "rain", "rain"],
                ["clear", "clear", "clear"]]
        result = propagate_weather(grid, 3, 3, iterations=1)
        assert len(result) == 3
        assert len(result[0]) == 3

    async def test_apply_and_get_weather(self, fresh_world_db):
        await apply_weather_to_world("happy", 0, 0)
        weather = await get_weather_at(0, 0)
        assert weather == "clear"

    async def test_default_weather(self, fresh_world_db):
        weather = await get_weather_at(99, 99)
        assert weather == "mild"


class TestEconomy:
    def test_biome_resources(self):
        assert "grain" in get_biome_resources("grassland")
        assert "timber" in get_biome_resources("woodland")
        assert get_biome_resources("nonexistent") == ["scraps"]

    def test_resource_values(self):
        assert RESOURCE_VALUES["gems"] > RESOURCE_VALUES["grain"]

    def test_find_path_trivial(self):
        grid = {(0, 0): 1, (1, 0): 1, (2, 0): 1}
        path = find_path(grid, (0, 0), (2, 0))
        assert path == [(0, 0), (1, 0), (2, 0)]

    def test_find_path_same_point(self):
        grid = {(0, 0): 1}
        path = find_path(grid, (0, 0), (0, 0))
        assert path == [(0, 0)]

    def test_find_path_no_path(self):
        grid = {(0, 0): 1, (5, 5): 1}
        path = find_path(grid, (0, 0), (5, 5))
        assert path is None

    def test_find_path_avoids_high_cost(self):
        grid = {
            (0, 0): 1, (1, 0): 100, (2, 0): 1,
            (0, 1): 1, (1, 1): 1, (2, 1): 1,
        }
        path = find_path(grid, (0, 0), (2, 0))
        assert path is not None
        # Should prefer going through (0,1),(1,1),(2,1),(2,0) over (1,0)
        assert (1, 0) not in path


class TestNPCs:
    def test_extract_proper_nouns(self):
        names = extract_proper_nouns("My Day", "I met Alice at the park. Then Bob arrived.")
        assert "Alice" in names or "Bob" in names

    def test_extract_no_common_words(self):
        names = extract_proper_nouns("Today", "The weather was nice. January is cold.")
        # Common words like "January", "Today" should be excluded
        lowers = {n.lower() for n in names}
        assert "january" not in lowers
        assert "today" not in lowers

    def test_generate_npc_name_with_nouns(self):
        name = generate_npc_name(1, 0, ["Alice", "Bob"])
        assert name == "Alice"

    def test_generate_npc_name_fallback(self):
        name = generate_npc_name(1, 0, [])
        assert isinstance(name, str)
        assert len(name) >= 3

    async def test_generate_npcs(self, fresh_world_db):
        # Place a tile so biome lookup works
        await world_db.set_tiles_batch([{
            "x": 16, "y": 6, "terrain": "field", "elevation": 0.5,
            "moisture": 0.5, "biome": "grassland", "entry_id": 1,
            "chunk_x": 0, "chunk_y": 0,
        }])
        npcs = await generate_npcs(1, "Meeting Alice", "Alice came to visit", "happy", 0, 0, 1)
        assert len(npcs) >= 1
        assert npcs[0]["personality"] == "cheerful"


class TestQuests:
    async def test_generate_quests(self, fresh_world_db):
        # Set up a settlement for quest context
        await world_db.insert_entity(16, 6, "settlement", "Test Town", entry_id=1)
        await world_db.set_tiles_batch([{
            "x": 16, "y": 6, "terrain": "field", "elevation": 0.5,
            "moisture": 0.5, "biome": "grassland", "entry_id": 1,
            "chunk_x": 0, "chunk_y": 0,
        }])
        quests = await generate_quests(1, "A Journey", "I want to travel and explore", "energetic", 0, 0, 1)
        assert len(quests) >= 1

    async def test_active_quests(self, fresh_world_db):
        await world_db.set_tiles_batch([{
            "x": 16, "y": 6, "terrain": "field", "elevation": 0.5,
            "moisture": 0.5, "biome": "grassland", "entry_id": 1,
            "chunk_x": 0, "chunk_y": 0,
        }])
        await generate_quests(1, "Travel", "Let us travel far", "happy", 0, 0, 1)
        active = await get_active_quests()
        assert len(active) >= 1

    async def test_complete_quest(self, fresh_world_db):
        await world_db.set_tiles_batch([{
            "x": 16, "y": 6, "terrain": "field", "elevation": 0.5,
            "moisture": 0.5, "biome": "grassland", "entry_id": 1,
            "chunk_x": 0, "chunk_y": 0,
        }])
        await generate_quests(1, "Explore", "Let us explore the lands", "happy", 0, 0, 1)
        # Complete quest by being near target
        completed = await complete_quest_at(16, 6, radius=5)
        assert completed is not None

        # Should have no active quests now (or fewer)
        active = await get_active_quests()
        for q in active:
            assert q.get("completed") is not True or q["id"] != completed["id"]
