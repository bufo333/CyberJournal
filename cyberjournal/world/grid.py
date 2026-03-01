# -*- coding: utf-8 -*-
"""World grid composition — maps entry chunks onto a persistent world grid.

Each entry's map becomes a "chunk" placed on the meta-grid.
Entry 1 at origin; subsequent entries placed adjacent to the most
content-similar existing entry (keyword overlap as similarity metric).
"""
from __future__ import annotations

import json
from typing import Optional

from cyberjournal.map import text_to_map, classify, noise, text_seed
from cyberjournal.world.biomes import classify_biome
from cyberjournal.world import world_db

CHUNK_W = 32
CHUNK_H = 12


async def get_chunk_placements() -> dict[int, tuple[int, int]]:
    """Return {entry_id: (chunk_x, chunk_y)} from metadata."""
    raw = await world_db.get_meta("chunk_placements")
    if not raw:
        return {}
    data = json.loads(raw)
    return {int(k): tuple(v) for k, v in data.items()}


async def save_chunk_placements(placements: dict[int, tuple[int, int]]) -> None:
    """Persist chunk placements to metadata."""
    data = {str(k): list(v) for k, v in placements.items()}
    await world_db.set_meta("chunk_placements", json.dumps(data))


def _keyword_set(title: str, body: str) -> set[str]:
    """Extract keyword set for similarity comparison."""
    import re
    words = re.findall(r"[a-z]{4,}", f"{title} {body}".lower())
    stop = {"the", "and", "for", "that", "with", "have", "this", "from", "your", "they",
            "were", "about", "would", "could", "should", "there", "their", "them", "into"}
    return {w for w in words if w not in stop}


def _similarity(kw1: set[str], kw2: set[str]) -> float:
    """Jaccard similarity between two keyword sets."""
    if not kw1 or not kw2:
        return 0.0
    return len(kw1 & kw2) / len(kw1 | kw2)


async def find_best_placement(
    entry_keywords: set[str],
    existing_placements: dict[int, tuple[int, int]],
    entry_keywords_map: dict[int, set[str]],
) -> tuple[int, int]:
    """Find the best chunk position for a new entry based on keyword similarity.

    Places adjacent to the most similar existing entry.
    """
    if not existing_placements:
        return (0, 0)

    # Find most similar existing entry
    best_eid = None
    best_sim = -1.0
    for eid, kws in entry_keywords_map.items():
        if eid in existing_placements:
            sim = _similarity(entry_keywords, kws)
            if sim > best_sim:
                best_sim = sim
                best_eid = eid

    if best_eid is None:
        # Just place at first available spot
        return (0, len(existing_placements))

    base_cx, base_cy = existing_placements[best_eid]
    occupied = set(existing_placements.values())

    # Try adjacent positions (right, down, left, up, diagonals)
    for dx, dy in [(1, 0), (0, 1), (-1, 0), (0, -1), (1, 1), (-1, 1), (1, -1), (-1, -1)]:
        pos = (base_cx + dx, base_cy + dy)
        if pos not in occupied:
            return pos

    # Fallback: spiral outward
    for r in range(2, 20):
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                pos = (base_cx + dx, base_cy + dy)
                if pos not in occupied:
                    return pos

    return (base_cx + 1, base_cy + 1)


async def generate_chunk(
    entry_id: int,
    title: str,
    body: str,
    chunk_x: int,
    chunk_y: int,
    turn: int = 0,
) -> None:
    """Generate terrain tiles for an entry and store in world DB.

    The chunk is placed at world coordinates (chunk_x * CHUNK_W, chunk_y * CHUNK_H).
    """
    text = f"{title}\n{body}".strip()
    _, types, costs, legend = text_to_map(text, width=CHUNK_W, height=CHUNK_H)

    seed = text_seed(text)
    world_x_offset = chunk_x * CHUNK_W
    world_y_offset = chunk_y * CHUNK_H

    tiles = []
    for y in range(CHUNK_H):
        for x in range(CHUNK_W):
            terrain = types[y][x]
            scale_e = 8.0 + (seed % 97) * 0.2
            scale_m = 10.0 + ((seed >> 16) % 89) * 0.25
            elev = noise(seed ^ 0xA57E, x, y, scale=scale_e, octaves=4, persistence=0.55)
            moist = noise(seed ^ 0xBEEF, x + 1000, y - 777, scale=scale_m, octaves=3, persistence=0.6)
            biome = classify_biome(terrain, elev, moist)
            tiles.append({
                "x": world_x_offset + x,
                "y": world_y_offset + y,
                "terrain": terrain,
                "elevation": elev,
                "moisture": moist,
                "biome": biome,
                "entry_id": entry_id,
                "chunk_x": chunk_x,
                "chunk_y": chunk_y,
            })

    await world_db.set_tiles_batch(tiles)


async def regenerate_entry_chunk(
    entry_id: int,
    title: str,
    body: str,
) -> None:
    """Regenerate the world chunk for a specific entry (on edit)."""
    placements = await get_chunk_placements()
    if entry_id not in placements:
        return  # Entry not in world yet

    chunk_x, chunk_y = placements[entry_id]
    await world_db.clear_tiles_for_entry(entry_id)
    await world_db.clear_entities_for_entry(entry_id)
    turn = await world_db.get_current_turn()
    await generate_chunk(entry_id, title, body, chunk_x, chunk_y, turn)


async def remove_entry_from_world(entry_id: int) -> None:
    """Remove an entry's chunk and entities from the world."""
    await world_db.clear_tiles_for_entry(entry_id)
    await world_db.clear_entities_for_entry(entry_id)
    placements = await get_chunk_placements()
    placements.pop(entry_id, None)
    await save_chunk_placements(placements)
