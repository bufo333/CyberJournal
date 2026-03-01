# -*- coding: utf-8 -*-
"""Event system — new entries trigger world events based on mood and keywords."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from cyberjournal.world import world_db


# Mood → event mappings
MOOD_EVENTS = {
    "happy": [
        ("festival", "A great festival breaks out in celebration!"),
        ("harvest", "A bountiful harvest enriches the land."),
    ],
    "sad": [
        ("storm", "Dark storm clouds gather over the region."),
        ("mourning", "A period of mourning settles across the land."),
    ],
    "anxious": [
        ("tremor", "The ground trembles with unease."),
        ("fog", "A thick fog rolls in, obscuring the paths."),
    ],
    "energetic": [
        ("construction", "New structures rise from the landscape."),
        ("expedition", "An expedition sets out to explore unknown lands."),
    ],
    "calm": [
        ("peace", "A serene peace falls over the world."),
        ("meditation", "The inhabitants enter a period of deep reflection."),
    ],
    "neutral": [
        ("trade", "Trade caravans move between settlements."),
    ],
}

# Keyword → event triggers
KEYWORD_EVENTS = {
    "travel": ("discovery", "New lands discovered through journeying"),
    "journey": ("discovery", "A great journey reveals hidden paths"),
    "battle": ("conflict", "Conflict erupts at the frontier"),
    "fight": ("conflict", "Skirmishes break out in the region"),
    "love": ("blessing", "Love's warmth spreads across the land"),
    "rain": ("weather_rain", "Rain nourishes the fields"),
    "snow": ("weather_snow", "Snow blankets the mountain passes"),
    "fire": ("disaster", "Fires sweep across the dry plains"),
    "build": ("construction", "New construction reshapes the landscape"),
    "dream": ("vision", "A prophetic vision is seen in the night sky"),
    "music": ("celebration", "Music fills the air throughout the land"),
    "death": ("loss", "A shadow of loss passes over the world"),
    "hope": ("dawn", "A new dawn breaks, full of promise"),
}


async def trigger_events(
    entry_id: int,
    title: str,
    body: str,
    mood: str,
    turn: int,
    chunk_x: int,
    chunk_y: int,
) -> list[dict]:
    """Generate world events from an entry. Returns list of generated events."""
    from cyberjournal.world.grid import CHUNK_W, CHUNK_H

    events = []
    world_x = chunk_x * CHUNK_W + CHUNK_W // 2
    world_y = chunk_y * CHUNK_H + CHUNK_H // 2
    now = datetime.now(timezone.utc).isoformat()

    # Mood-based events
    if mood in MOOD_EVENTS:
        event_type, description = MOOD_EVENTS[mood][turn % len(MOOD_EVENTS[mood])]
        eid = await world_db.insert_history_event(
            turn=turn, event_type=event_type, description=description,
            x=world_x, y=world_y, entry_id=entry_id, created_at=now,
        )
        events.append({"id": eid, "type": event_type, "description": description})

    # Keyword-based events
    text = f"{title} {body}".lower()
    words = set(re.findall(r"[a-z]{3,}", text))
    triggered = set()
    for keyword, (event_type, description) in KEYWORD_EVENTS.items():
        if keyword in words and event_type not in triggered:
            triggered.add(event_type)
            eid = await world_db.insert_history_event(
                turn=turn, event_type=event_type, description=description,
                x=world_x, y=world_y, entry_id=entry_id, created_at=now,
            )
            events.append({"id": eid, "type": event_type, "description": description})

    return events
