# -*- coding: utf-8 -*-
"""Day/night cycle — time of day affects world rendering palette."""
from __future__ import annotations

import json

from cyberjournal.world import world_db

DEFAULT_TIME = {"hour": 8, "minute": 0, "day": 1}

# Minutes advanced per player move
MINUTES_PER_MOVE = 30


async def get_world_time() -> dict:
    """Get current world time."""
    raw = await world_db.get_meta("world_time")
    if raw:
        return json.loads(raw)
    return DEFAULT_TIME.copy()


async def advance_time(moves: int = 1) -> dict:
    """Advance world time by move count. Returns updated time."""
    t = await get_world_time()
    total_minutes = t["hour"] * 60 + t["minute"] + (MINUTES_PER_MOVE * moves)
    # Handle day rollover
    days_passed = total_minutes // (24 * 60)
    remaining = total_minutes % (24 * 60)
    t["day"] = t.get("day", 1) + days_passed
    t["hour"] = remaining // 60
    t["minute"] = remaining % 60
    await world_db.set_meta("world_time", json.dumps(t))
    return t


def get_time_period(hour: int) -> str:
    """Map hour to time period."""
    if 5 <= hour < 7:
        return "dawn"
    elif 7 <= hour < 18:
        return "day"
    elif 18 <= hour < 20:
        return "dusk"
    else:
        return "night"


# Color palettes per time period — ANSI color codes
# These override the default PALETTE from map.py
PALETTES = {
    "dawn": {
        "water": "34",     # blue
        "shore": "33",     # yellow
        "river": "34",     # blue
        "road": "37",      # white
        "poi": "35",       # magenta
        "mount": "37",     # white
        "hill": "33",      # yellow
        "forest": "32",    # green
        "field": "33",     # yellow-warm
        "unknown": "37",   # white
    },
    "day": {
        "water": "36",     # cyan
        "shore": "33",     # yellow
        "river": "36",     # cyan
        "road": "37",      # white
        "poi": "31",       # red
        "mount": "37",     # white
        "hill": "32",      # green
        "forest": "32",    # green
        "field": "33",     # yellow
        "unknown": "37",   # white
    },
    "dusk": {
        "water": "34",     # blue
        "shore": "35",     # magenta
        "river": "34",     # blue
        "road": "90",      # dark gray
        "poi": "35",       # magenta
        "mount": "90",     # dark gray
        "hill": "35",      # magenta
        "forest": "32",    # green
        "field": "35",     # magenta
        "unknown": "90",   # dark gray
    },
    "night": {
        "water": "34",     # dark blue
        "shore": "90",     # dark gray
        "river": "34",     # dark blue
        "road": "90",      # dark gray
        "poi": "94",       # bright blue
        "mount": "90",     # dark gray
        "hill": "90",      # dark gray
        "forest": "90",    # dark gray
        "field": "90",     # dark gray
        "unknown": "90",   # dark gray
    },
}


def get_palette_for_time(hour: int) -> dict[str, str]:
    """Get the color palette for a given hour."""
    period = get_time_period(hour)
    return PALETTES.get(period, PALETTES["day"])


def format_time(t: dict) -> str:
    """Format time for display."""
    period = get_time_period(t["hour"])
    return f"Day {t.get('day', 1)}  {t['hour']:02d}:{t['minute']:02d}  [{period}]"
