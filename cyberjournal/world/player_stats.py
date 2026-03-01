# -*- coding: utf-8 -*-
"""Player stats and leveling system for the world explorer."""
from __future__ import annotations

import json

from cyberjournal.world import world_db

XP_TABLE = [0, 100, 250, 500, 900, 1400, 2100, 3000, 4200, 5800, 8000]

XP_AWARDS = {
    "tiles_explored": 1,
    "quests_completed": 50,
    "discoveries_found": 30,
    "entries_written": 20,
    "battles_won": 40,
    "items_crafted": 15,
    "items_traded": 5,
}

DEFAULT_STATS = {
    "xp": 0,
    "level": 0,
    "tiles_explored": 0,
    "quests_completed": 0,
    "discoveries_found": 0,
    "entries_written": 0,
    "battles_won": 0,
    "battles_fled": 0,
    "items_crafted": 0,
    "items_traded": 0,
    "structures_built": 0,
}


async def get_stats() -> dict:
    """Get the player's current stats."""
    raw = await world_db.get_meta("player_stats")
    if raw:
        stats = json.loads(raw)
        # Merge with defaults for any missing keys
        for k, v in DEFAULT_STATS.items():
            stats.setdefault(k, v)
        return stats
    return DEFAULT_STATS.copy()


async def _save_stats(stats: dict) -> None:
    """Persist player stats."""
    await world_db.set_meta("player_stats", json.dumps(stats))


def _level_for_xp(xp: int) -> int:
    """Calculate level from total XP."""
    level = 0
    for i, threshold in enumerate(XP_TABLE):
        if xp >= threshold:
            level = i
        else:
            break
    return level


def _xp_for_next_level(level: int) -> int:
    """XP needed to reach the next level."""
    if level + 1 < len(XP_TABLE):
        return XP_TABLE[level + 1]
    return XP_TABLE[-1] + (level + 1 - len(XP_TABLE) + 1) * 3000


async def award_xp(reason: str, amount: int | None = None) -> tuple[int, bool]:
    """Award XP. Returns (new_xp, did_level_up)."""
    stats = await get_stats()
    if amount is None:
        amount = XP_AWARDS.get(reason, 0)
    old_level = stats["level"]
    stats["xp"] = stats.get("xp", 0) + amount
    stats["level"] = _level_for_xp(stats["xp"])
    did_level_up = stats["level"] > old_level
    await _save_stats(stats)
    return stats["xp"], did_level_up


async def increment_stat(key: str, amount: int = 1) -> int:
    """Increment a stat counter and award associated XP. Returns new value."""
    stats = await get_stats()
    stats[key] = stats.get(key, 0) + amount
    # Award XP for this action
    xp_amount = XP_AWARDS.get(key, 0) * amount
    if xp_amount > 0:
        old_level = stats["level"]
        stats["xp"] = stats.get("xp", 0) + xp_amount
        stats["level"] = _level_for_xp(stats["xp"])
    await _save_stats(stats)
    return stats[key]


def format_stats(stats: dict) -> str:
    """Format full stats sheet for display."""
    level = stats.get("level", 0)
    xp = stats.get("xp", 0)
    next_xp = _xp_for_next_level(level)
    lines = [
        "PLAYER STATS",
        "=" * 30,
        f"  Level: {level}",
        f"  XP: {xp} / {next_xp}",
        "",
        "EXPLORATION",
        f"  Tiles explored: {stats.get('tiles_explored', 0)}",
        f"  Discoveries: {stats.get('discoveries_found', 0)}",
        f"  Quests completed: {stats.get('quests_completed', 0)}",
        "",
        "COMBAT",
        f"  Battles won: {stats.get('battles_won', 0)}",
        f"  Times fled: {stats.get('battles_fled', 0)}",
        "",
        "CRAFTING & TRADE",
        f"  Items crafted: {stats.get('items_crafted', 0)}",
        f"  Items traded: {stats.get('items_traded', 0)}",
        f"  Structures built: {stats.get('structures_built', 0)}",
        "",
        "JOURNAL",
        f"  Entries written: {stats.get('entries_written', 0)}",
    ]
    return "\n".join(lines)


def format_hud(stats: dict) -> str:
    """Format one-line HUD for the info bar."""
    level = stats.get("level", 0)
    xp = stats.get("xp", 0)
    next_xp = _xp_for_next_level(level)
    explored = stats.get("tiles_explored", 0)
    return f"Lv{level} | XP {xp}/{next_xp} | Explored: {explored}"
