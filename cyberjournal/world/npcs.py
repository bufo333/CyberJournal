# -*- coding: utf-8 -*-
"""NPC generation — extract proper nouns from entries as NPC names."""
from __future__ import annotations

import json
import re
from typing import Optional

from cyberjournal.world import world_db


# Mood → personality trait
MOOD_PERSONALITY = {
    "happy": "cheerful",
    "sad": "melancholic",
    "anxious": "nervous",
    "energetic": "adventurous",
    "calm": "wise",
    "neutral": "stoic",
}

# NPC roles based on nearby biome
BIOME_ROLES = {
    "grassland": ["farmer", "shepherd"],
    "woodland": ["woodcutter", "herbalist"],
    "temperate_forest": ["ranger", "hunter"],
    "rainforest": ["explorer", "botanist"],
    "marsh": ["fisher", "healer"],
    "savanna": ["herder", "trader"],
    "desert": ["miner", "nomad"],
    "alpine": ["prospector", "hermit"],
    "rocky_peaks": ["stonecutter", "sentinel"],
    "highland_forest": ["forester", "mystic"],
    "steppe": ["rider", "scout"],
    "badlands": ["scavenger", "outcast"],
    "beach": ["fisher", "sailor"],
    "riverbank": ["fisher", "boatwright"],
    "ocean": ["sailor"],
}

# Common words to exclude from NPC name extraction
_COMMON_WORDS = {
    "the", "and", "for", "that", "with", "have", "this", "from", "your", "they",
    "were", "about", "would", "could", "should", "there", "their", "them", "into",
    "been", "some", "just", "very", "also", "much", "then", "when", "what", "each",
    "today", "yesterday", "tomorrow", "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "sunday", "january", "february", "march", "april", "may",
    "june", "july", "august", "september", "october", "november", "december",
}


def extract_proper_nouns(title: str, body: str) -> list[str]:
    """Extract capitalized words that look like proper nouns from entry text.

    Returns up to 3 unique names.
    """
    text = f"{title}\n{body}"
    # Find capitalized words not at sentence start
    # Pattern: word boundary, uppercase letter, then lowercase letters
    candidates = re.findall(r"(?<=[.!?\n]\s)([A-Z][a-z]{2,})", text)
    # Also grab mid-sentence capitalized words
    candidates += re.findall(r"(?<=\s)([A-Z][a-z]{2,})(?=\s)", text)

    seen = set()
    names = []
    for name in candidates:
        lower = name.lower()
        if lower not in _COMMON_WORDS and lower not in seen and len(name) >= 3:
            seen.add(lower)
            names.append(name)
            if len(names) >= 3:
                break

    return names


def generate_npc_name(entry_id: int, index: int, proper_nouns: list[str]) -> str:
    """Generate an NPC name from proper nouns or a fallback."""
    if index < len(proper_nouns):
        return proper_nouns[index]
    # Fallback names based on entry_id and index
    fallback_names = [
        "Aldric", "Brenna", "Caelum", "Dara", "Eirik", "Freya",
        "Gareth", "Hilde", "Ivar", "Jorun", "Kael", "Liara",
        "Maren", "Nils", "Orla", "Peri", "Quinn", "Rowan",
        "Signe", "Theron", "Una", "Valen", "Wren", "Xara",
    ]
    idx = (entry_id * 7 + index * 13) % len(fallback_names)
    return fallback_names[idx]


async def generate_npcs(
    entry_id: int,
    title: str,
    body: str,
    mood: str,
    chunk_x: int,
    chunk_y: int,
    turn: int,
) -> list[dict]:
    """Generate NPCs from an entry and place them near the entry's settlement.

    Returns list of generated NPC dicts.
    """
    from cyberjournal.world.grid import CHUNK_W, CHUNK_H

    proper_nouns = extract_proper_nouns(title, body)
    personality = MOOD_PERSONALITY.get(mood, "stoic")
    word_count = len(body.split())

    # Generate 1-2 NPCs depending on entry length
    npc_count = 1 if word_count < 100 else 2
    world_x = chunk_x * CHUNK_W + CHUNK_W // 2
    world_y = chunk_y * CHUNK_H + CHUNK_H // 2

    # Get biome at NPC position for role assignment
    tile = await world_db.get_tile(world_x, world_y)
    biome = tile["biome"] if tile else "grassland"
    roles = BIOME_ROLES.get(biome, ["villager"])

    npcs = []
    for i in range(npc_count):
        name = generate_npc_name(entry_id, i, proper_nouns)
        role = roles[i % len(roles)]
        # Offset NPC position slightly from center
        nx = world_x + (i * 2 - 1)
        ny = world_y + 1

        props = json.dumps({
            "personality": personality,
            "role": role,
            "source_mood": mood,
        })

        eid = await world_db.insert_entity(
            nx, ny, "npc", f"{name} the {role.title()}",
            properties=props, entry_id=entry_id, turn=turn,
        )
        npcs.append({
            "id": eid,
            "name": name,
            "role": role,
            "personality": personality,
            "x": nx, "y": ny,
        })

    return npcs
