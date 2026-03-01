# -*- coding: utf-8 -*-
"""Weather system — mood-driven weather with cellular automaton propagation."""
from __future__ import annotations

from cyberjournal.world import world_db

# Mood → weather condition mapping
MOOD_WEATHER = {
    "happy": "clear",
    "sad": "rain",
    "anxious": "storm",
    "energetic": "windy",
    "calm": "mild",
    "neutral": "overcast",
}

WEATHER_SYMBOLS = {
    "clear": ".",
    "mild": "~",
    "overcast": "=",
    "rain": "/",
    "storm": "*",
    "windy": ">",
    "snow": "#",
}


def mood_to_weather(mood: str) -> str:
    """Map a mood string to a weather condition."""
    return MOOD_WEATHER.get(mood, "mild")


def propagate_weather(
    weather_grid: list[list[str]],
    width: int,
    height: int,
    iterations: int = 3,
) -> list[list[str]]:
    """Simple cellular automaton weather propagation.

    Each cell takes the most common weather among itself and its 4 neighbors.
    """
    grid = [row[:] for row in weather_grid]

    for _ in range(iterations):
        new_grid = [row[:] for row in grid]
        for y in range(height):
            for x in range(width):
                neighbors = [grid[y][x]]
                for nx, ny in [(x-1, y), (x+1, y), (x, y-1), (x, y+1)]:
                    if 0 <= nx < width and 0 <= ny < height:
                        neighbors.append(grid[ny][nx])
                # Most common weather wins
                counts: dict[str, int] = {}
                for w in neighbors:
                    counts[w] = counts.get(w, 0) + 1
                new_grid[y][x] = max(counts, key=counts.get)
        grid = new_grid

    return grid


async def apply_weather_to_world(
    mood: str,
    chunk_x: int,
    chunk_y: int,
    chunk_w: int = 32,
    chunk_h: int = 12,
) -> None:
    """Apply weather effects to the world around a chunk based on entry mood.

    Sets a weather metadata key that the renderer can use.
    """
    weather = mood_to_weather(mood)
    import json
    raw = await world_db.get_meta("world_weather")
    weather_map = json.loads(raw) if raw else {}
    weather_map[f"{chunk_x},{chunk_y}"] = weather
    await world_db.set_meta("world_weather", json.dumps(weather_map))


async def get_weather_at(chunk_x: int, chunk_y: int) -> str:
    """Get the current weather at a chunk position."""
    import json
    raw = await world_db.get_meta("world_weather")
    if not raw:
        return "mild"
    weather_map = json.loads(raw)
    return weather_map.get(f"{chunk_x},{chunk_y}", "mild")
