# -*- coding: utf-8 -*-
"""Quest generation — template quests from entry themes."""
from __future__ import annotations

import json
import re
from typing import Optional

from cyberjournal.world import world_db

# Quest templates: (quest_type, title_template, description_template)
# Placeholders: {keyword}, {settlement}, {resource}, {biome}
QUEST_TEMPLATES = {
    "explore": [
        ("Explore the {keyword} Lands", "Journey to the {biome} region and discover what lies beyond."),
        ("Seek the {keyword}", "Rumours speak of {keyword} in distant lands. Go find the truth."),
    ],
    "deliver": [
        ("Deliver {resource} to {settlement}", "The people of {settlement} need {resource}. Bring it to them."),
        ("{resource} Caravan", "Escort a caravan of {resource} safely to {settlement}."),
    ],
    "discover": [
        ("The Lost {keyword}", "Ancient texts mention a lost {keyword}. Search the {biome} for clues."),
        ("Uncharted {biome}", "Map the unexplored {biome} territory to the north."),
    ],
    "protect": [
        ("Defend {settlement}", "Dark forces threaten {settlement}. Rally the defenders."),
        ("Guard the {resource}", "Protect the {resource} stores from raiders."),
    ],
    "gather": [
        ("Harvest {resource}", "Gather {resource} from the surrounding {biome} lands."),
        ("{keyword} Collection", "Collect samples of {keyword} for the scholars."),
    ],
}

# Keywords that trigger specific quest types
KEYWORD_QUEST_MAP = {
    "travel": "explore",
    "journey": "explore",
    "explore": "explore",
    "discover": "discover",
    "find": "discover",
    "search": "discover",
    "deliver": "deliver",
    "bring": "deliver",
    "trade": "deliver",
    "protect": "protect",
    "defend": "protect",
    "guard": "protect",
    "gather": "gather",
    "collect": "gather",
    "harvest": "gather",
    "build": "gather",
}


async def generate_quests(
    entry_id: int,
    title: str,
    body: str,
    mood: str,
    chunk_x: int,
    chunk_y: int,
    turn: int,
) -> list[dict]:
    """Generate quests from an entry's content. Returns list of quest dicts."""
    from cyberjournal.world.grid import CHUNK_W, CHUNK_H
    from cyberjournal.world.economy import get_biome_resources, RESOURCE_VALUES

    text = f"{title} {body}".lower()
    words = set(re.findall(r"[a-z]{3,}", text))

    # Find triggered quest types
    triggered_types: dict[str, str] = {}  # quest_type -> triggering keyword
    for word in words:
        if word in KEYWORD_QUEST_MAP:
            qt = KEYWORD_QUEST_MAP[word]
            if qt not in triggered_types:
                triggered_types[qt] = word

    if not triggered_types:
        # Default: generate one explore quest
        triggered_types["explore"] = title.split()[0].lower() if title else "unknown"

    # Get nearby settlement and resource info
    world_x = chunk_x * CHUNK_W + CHUNK_W // 2
    world_y = chunk_y * CHUNK_H + CHUNK_H // 2

    entities = await world_db.get_entities_in_rect(
        world_x - CHUNK_W, world_y - CHUNK_H,
        world_x + CHUNK_W, world_y + CHUNK_H,
    )
    settlement_name = "the settlement"
    for e in entities:
        if e["type"] == "settlement":
            settlement_name = e["name"]
            break

    tile = await world_db.get_tile(world_x, world_y)
    biome = tile["biome"] if tile else "grassland"
    resources = get_biome_resources(biome)
    resource = resources[0] if resources else "goods"

    # Generate quests
    quests = []
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    for quest_type, keyword in triggered_types.items():
        templates = QUEST_TEMPLATES.get(quest_type, QUEST_TEMPLATES["explore"])
        template_idx = turn % len(templates)
        quest_title_t, quest_desc_t = templates[template_idx]

        quest_title = quest_title_t.format(
            keyword=keyword.title(), settlement=settlement_name,
            resource=resource, biome=biome,
        )
        quest_desc = quest_desc_t.format(
            keyword=keyword, settlement=settlement_name,
            resource=resource, biome=biome,
        )

        # Store quest as a history event with quest metadata
        quest_data = {
            "quest_type": quest_type,
            "title": quest_title,
            "target_x": world_x,
            "target_y": world_y,
            "completed": False,
        }

        eid = await world_db.insert_history_event(
            turn=turn,
            event_type=f"quest_{quest_type}",
            description=f"Quest: {quest_title} — {quest_desc}",
            x=world_x, y=world_y,
            entry_id=entry_id,
            created_at=now,
        )

        quests.append({
            "id": eid,
            "type": quest_type,
            "title": quest_title,
            "description": quest_desc,
            "target_x": world_x,
            "target_y": world_y,
        })

        if len(quests) >= 2:
            break

    # Store active quests in metadata
    raw = await world_db.get_meta("active_quests")
    active = json.loads(raw) if raw else []
    for q in quests:
        active.append({
            "id": q["id"],
            "type": q["type"],
            "title": q["title"],
            "description": q["description"],
            "target_x": q["target_x"],
            "target_y": q["target_y"],
            "completed": False,
        })
    await world_db.set_meta("active_quests", json.dumps(active))

    return quests


async def get_active_quests() -> list[dict]:
    """Return all active (incomplete) quests."""
    raw = await world_db.get_meta("active_quests")
    if not raw:
        return []
    quests = json.loads(raw)
    return [q for q in quests if not q.get("completed")]


async def complete_quest_at(x: int, y: int, radius: int = 3) -> Optional[dict]:
    """Mark a quest as completed if the cursor is near its target. Returns the quest or None."""
    raw = await world_db.get_meta("active_quests")
    if not raw:
        return None

    quests = json.loads(raw)
    completed = None
    for q in quests:
        if q.get("completed"):
            continue
        dx = abs(q["target_x"] - x)
        dy = abs(q["target_y"] - y)
        if dx <= radius and dy <= radius:
            q["completed"] = True
            completed = q
            break

    if completed:
        await world_db.set_meta("active_quests", json.dumps(quests))
    return completed
