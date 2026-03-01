# -*- coding: utf-8 -*-
"""Time progression — each entry = one world turn."""
from __future__ import annotations

from datetime import datetime, timezone

from cyberjournal.world import world_db


ERA_THRESHOLDS = [
    (10, "Dawn Age"),
    (25, "Age of Exploration"),
    (50, "Age of Growth"),
    (100, "Age of Civilization"),
    (200, "Age of Prosperity"),
]


def get_era(turn: int) -> str:
    """Return the era name for a given turn number."""
    for threshold, name in reversed(ERA_THRESHOLDS):
        if turn >= threshold:
            return name
    return "Primordial Era"


async def advance_turn(entry_id: int, title: str) -> int:
    """Advance the world by one turn when a new entry is created. Returns new turn."""
    turn = await world_db.get_current_turn()
    turn += 1
    await world_db.set_meta("current_turn", str(turn))

    era = get_era(turn)
    created_at = datetime.now(timezone.utc).isoformat()
    await world_db.insert_history_event(
        turn=turn,
        event_type="new_entry",
        description=f"Turn {turn} ({era}): '{title}' inscribed into the world",
        entry_id=entry_id,
        created_at=created_at,
    )
    return turn


async def get_world_timeline(limit: int = 50) -> list[dict]:
    """Return the world history as a list of event dicts."""
    rows = await world_db.get_history(limit)
    events = []
    for r in rows:
        events.append({
            "turn": r["turn"],
            "era": get_era(r["turn"]),
            "event_type": r["event_type"],
            "description": r["description"],
            "x": r["x"],
            "y": r["y"],
            "entry_id": r["entry_id"],
            "created_at": r["created_at"],
        })
    return events
