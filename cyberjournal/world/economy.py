# -*- coding: utf-8 -*-
"""Economy & resource system — settlements produce resources per biome."""
from __future__ import annotations

import heapq
import json
from typing import Optional

from cyberjournal.world import world_db

# Biome → resource production
BIOME_RESOURCES = {
    "grassland": ["grain", "livestock"],
    "woodland": ["timber", "herbs"],
    "temperate_forest": ["timber", "game"],
    "rainforest": ["exotic_plants", "timber"],
    "marsh": ["peat", "fish"],
    "savanna": ["livestock", "hides"],
    "desert": ["minerals", "salt"],
    "alpine": ["ore", "gems"],
    "rocky_peaks": ["stone", "ore"],
    "highland_forest": ["timber", "mushrooms"],
    "steppe": ["horses", "wool"],
    "badlands": ["clay", "minerals"],
    "beach": ["fish", "shells"],
    "riverbank": ["fish", "clay"],
    "ocean": ["fish"],
}

RESOURCE_VALUES = {
    "grain": 1,
    "livestock": 2,
    "timber": 2,
    "herbs": 3,
    "game": 2,
    "exotic_plants": 5,
    "peat": 1,
    "fish": 1,
    "hides": 2,
    "minerals": 4,
    "salt": 3,
    "ore": 4,
    "gems": 8,
    "stone": 2,
    "mushrooms": 2,
    "horses": 5,
    "wool": 2,
    "clay": 1,
    "shells": 1,
}


def get_biome_resources(biome: str) -> list[str]:
    """Return the resources produced by a given biome."""
    return BIOME_RESOURCES.get(biome, ["scraps"])


async def get_settlement_production(entity_id: int) -> list[str]:
    """Determine what resources a settlement produces based on surrounding biomes."""
    async with __import__("aiosqlite").connect(world_db.WORLD_DB_PATH) as conn:
        conn.row_factory = __import__("aiosqlite").Row
        cur = await conn.execute(
            "SELECT x, y FROM world_entities WHERE id = ?", (entity_id,)
        )
        ent = await cur.fetchone()
        await cur.close()
        if not ent:
            return []

    # Check surrounding tiles
    x, y = ent["x"], ent["y"]
    tiles = await world_db.get_tiles_in_rect(x - 3, y - 3, x + 3, y + 3)
    resources = set()
    for t in tiles:
        for r in get_biome_resources(t["biome"]):
            resources.add(r)
    return sorted(resources)


def find_path(
    cost_grid: dict[tuple[int, int], float],
    start: tuple[int, int],
    end: tuple[int, int],
) -> list[tuple[int, int]] | None:
    """A* pathfinding on the cost grid for trade routes.

    cost_grid: {(x,y): movement_cost} — missing means impassable.
    Returns list of (x,y) positions from start to end, or None if no path.
    """
    if start == end:
        return [start]

    open_set: list[tuple[float, tuple[int, int]]] = [(0, start)]
    came_from: dict[tuple[int, int], tuple[int, int]] = {}
    g_score: dict[tuple[int, int], float] = {start: 0}

    def heuristic(a: tuple[int, int], b: tuple[int, int]) -> float:
        return abs(a[0] - b[0]) + abs(a[1] - b[1])

    while open_set:
        _, current = heapq.heappop(open_set)

        if current == end:
            path = [current]
            while current in came_from:
                current = came_from[current]
                path.append(current)
            return list(reversed(path))

        for dx, dy in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
            neighbor = (current[0] + dx, current[1] + dy)
            if neighbor not in cost_grid:
                continue
            tentative = g_score[current] + cost_grid[neighbor]
            if tentative < g_score.get(neighbor, float("inf")):
                came_from[neighbor] = current
                g_score[neighbor] = tentative
                f = tentative + heuristic(neighbor, end)
                heapq.heappush(open_set, (f, neighbor))

    return None


async def generate_trade_routes() -> list[list[tuple[int, int]]]:
    """Generate trade routes between settlements using pathfinding."""
    import aiosqlite
    async with aiosqlite.connect(world_db.WORLD_DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT id, x, y, name FROM world_entities WHERE type = 'settlement'"
        )
        settlements = await cur.fetchall()
        await cur.close()

    if len(settlements) < 2:
        return []

    # Build cost grid from tiles
    async with aiosqlite.connect(world_db.WORLD_DB_PATH) as conn:
        cur = await conn.execute("SELECT x, y, terrain FROM world_tiles")
        rows = await cur.fetchall()
        await cur.close()

    from cyberjournal.map import TERRAIN_COST
    cost_grid = {}
    for r in rows:
        cost = TERRAIN_COST.get(r[2], 1)
        if cost < float("inf"):
            cost_grid[(r[0], r[1])] = cost

    routes = []
    for i in range(len(settlements)):
        for j in range(i + 1, len(settlements)):
            start = (settlements[i]["x"], settlements[i]["y"])
            end = (settlements[j]["x"], settlements[j]["y"])
            path = find_path(cost_grid, start, end)
            if path:
                routes.append(path)

    return routes
