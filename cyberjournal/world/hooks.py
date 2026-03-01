# -*- coding: utf-8 -*-
"""Integration hooks — called when entries are created/edited/deleted.

These functions bridge the journal system with the world simulation.
"""
from __future__ import annotations

import json
import logging

from cyberjournal.world import world_db
from cyberjournal.world.grid import (
    CHUNK_W,
    CHUNK_H,
    generate_chunk,
    get_chunk_placements,
    save_chunk_placements,
    find_best_placement,
    regenerate_entry_chunk,
    remove_entry_from_world,
    _keyword_set,
)
from cyberjournal.world.timeline import advance_turn
from cyberjournal.world.biomes import derive_entity_type
from cyberjournal.world.events import trigger_events
from cyberjournal.world.weather import apply_weather_to_world
from cyberjournal.world.npcs import generate_npcs
from cyberjournal.world.quests import generate_quests

logger = logging.getLogger(__name__)


async def on_entry_created(
    entry_id: int,
    title: str,
    body: str,
    word_count: int = 0,
    mood: str = "",
) -> None:
    """Called after a new entry is created. Generates a world chunk and entities."""
    try:
        await world_db.init_world_db()

        # Advance world turn
        turn = await advance_turn(entry_id, title)

        # Determine chunk placement
        placements = await get_chunk_placements()
        entry_kws = _keyword_set(title, body)

        # Build keyword map for existing entries (from metadata)
        raw_kw_map = await world_db.get_meta("entry_keywords")
        kw_map: dict[int, set[str]] = {}
        if raw_kw_map:
            for k, v in json.loads(raw_kw_map).items():
                kw_map[int(k)] = set(v)

        chunk_pos = await find_best_placement(entry_kws, placements, kw_map)
        placements[entry_id] = chunk_pos
        await save_chunk_placements(placements)

        # Save keyword map
        kw_map[entry_id] = entry_kws
        kw_data = {str(k): list(v) for k, v in kw_map.items()}
        await world_db.set_meta("entry_keywords", json.dumps(kw_data))

        # Generate chunk tiles
        await generate_chunk(entry_id, title, body, chunk_pos[0], chunk_pos[1], turn)

        # Generate entity
        entity_type = derive_entity_type(word_count, mood, "field")
        world_x = chunk_pos[0] * CHUNK_W + CHUNK_W // 2
        world_y = chunk_pos[1] * CHUNK_H + CHUNK_H // 2
        entity_name = title[:30] if title else f"Place #{entry_id}"
        props = json.dumps({"mood": mood, "word_count": word_count})
        await world_db.insert_entity(
            world_x, world_y, entity_type, entity_name,
            properties=props, entry_id=entry_id, turn=turn,
        )

        # Record history event
        from datetime import datetime, timezone
        await world_db.insert_history_event(
            turn=turn,
            event_type="chunk_placed",
            description=f"New land '{title}' appeared at chunk ({chunk_pos[0]}, {chunk_pos[1]})",
            x=world_x, y=world_y,
            entry_id=entry_id,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        # Trigger world events based on mood and keywords
        await trigger_events(entry_id, title, body, mood, turn, chunk_pos[0], chunk_pos[1])

        # Apply weather effects
        await apply_weather_to_world(mood, chunk_pos[0], chunk_pos[1])

        # Generate NPCs from entry content
        await generate_npcs(entry_id, title, body, mood, chunk_pos[0], chunk_pos[1], turn)

        # Generate quests from entry themes
        await generate_quests(entry_id, title, body, mood, chunk_pos[0], chunk_pos[1], turn)

        # Award XP for writing an entry
        try:
            from cyberjournal.world.player_stats import increment_stat
            await increment_stat("entries_written")
        except Exception:
            logger.debug("Could not award entry XP")

        # Place hidden landmarks for discovery mechanics
        # Older entries become "ancient texts" discoverable near their chunk edges
        from cyberjournal.map import text_seed
        seed = text_seed(f"{title}\n{body}")
        if seed % 3 == 0:  # ~33% chance of hidden landmark per entry
            hx = chunk_pos[0] * CHUNK_W + (seed % CHUNK_W)
            hy = chunk_pos[1] * CHUNK_H + (seed >> 8) % CHUNK_H
            await world_db.insert_entity(
                hx, hy, "hidden_landmark",
                f"Hidden {title[:20]}",
                properties=json.dumps({"ancient_text": True}),
                entry_id=entry_id, turn=turn,
            )

    except Exception:
        logger.exception("World hook on_entry_created failed (non-fatal)")


async def on_entry_edited(entry_id: int, title: str, body: str) -> None:
    """Called after an entry is edited. Regenerates the world chunk."""
    try:
        await world_db.init_world_db()
        await regenerate_entry_chunk(entry_id, title, body)
    except Exception:
        logger.exception("World hook on_entry_edited failed (non-fatal)")


async def on_entry_deleted(entry_id: int) -> None:
    """Called after an entry is deleted. Removes its chunk from the world."""
    try:
        await world_db.init_world_db()
        await remove_entry_from_world(entry_id)
    except Exception:
        logger.exception("World hook on_entry_deleted failed (non-fatal)")
