# -*- coding: utf-8 -*-
"""Tile-based world renderer for the Textual TUI.

Renders a viewport of the world map with cursor, using the existing PALETTE colors.
"""
from __future__ import annotations

from cyberjournal.map import PALETTE, SYMBOLS_UTF, SYMBOLS_ASCII, _fg, ANSI_RESET
from cyberjournal.world.biomes import ENTITY_TYPES


# Map biome → terrain type for rendering
BIOME_TO_TERRAIN = {
    "ocean": "water",
    "beach": "shore",
    "riverbank": "river",
    "road": "road",
    "landmark": "poi",
    "alpine": "mount",
    "rocky_peaks": "mount",
    "highland_forest": "forest",
    "steppe": "hill",
    "badlands": "hill",
    "rainforest": "forest",
    "temperate_forest": "forest",
    "woodland": "forest",
    "marsh": "shore",
    "grassland": "field",
    "savanna": "field",
    "desert": "field",
}


def render_world_viewport(
    tiles: list,
    entities: list,
    viewport_x: int,
    viewport_y: int,
    viewport_w: int,
    viewport_h: int,
    cursor_x: int,
    cursor_y: int,
    color: bool = False,
    charset: str = "utf",
) -> str:
    """Render a rectangular viewport of the world.

    tiles: list of tile rows from world_db
    entities: list of entity rows from world_db
    Returns a string suitable for Static widget display.
    """
    sym = SYMBOLS_UTF if charset == "utf" else SYMBOLS_ASCII

    # Build lookup dictionaries
    tile_map: dict[tuple[int, int], dict] = {}
    for t in tiles:
        tile_map[(t["x"], t["y"])] = {
            "terrain": t["terrain"],
            "biome": t["biome"],
            "elevation": t["elevation"],
        }

    entity_map: dict[tuple[int, int], dict] = {}
    for e in entities:
        entity_map[(e["x"], e["y"])] = {
            "type": e["type"],
            "name": e["name"],
        }

    lines = []
    for dy in range(viewport_h):
        wy = viewport_y + dy
        row_chars = []
        for dx in range(viewport_w):
            wx = viewport_x + dx
            is_cursor = (wx == cursor_x and wy == cursor_y)

            # Check for entity first
            if (wx, wy) in entity_map:
                ent = entity_map[(wx, wy)]
                ch = ENTITY_TYPES.get(ent["type"], {}).get("symbol", "?")
                tile_type = "poi"
            elif (wx, wy) in tile_map:
                tile = tile_map[(wx, wy)]
                terrain = tile["terrain"]
                ch = sym.get(terrain, "?")
                tile_type = terrain
            else:
                ch = " "
                tile_type = "unknown"

            if is_cursor:
                if color:
                    row_chars.append(f"\x1b[7m{ch}\x1b[0m")  # inverse video
                else:
                    row_chars.append("@")
            elif color and tile_type != "unknown":
                code = PALETTE.get(tile_type, PALETTE.get("unknown", "37"))
                row_chars.append(f"{_fg(code)}{ch}{ANSI_RESET}")
            else:
                row_chars.append(ch)

        lines.append("".join(row_chars))

    return "\n".join(lines)


def render_tile_info(
    tile: dict | None,
    entity: dict | None,
    cursor_x: int,
    cursor_y: int,
) -> str:
    """Render info panel text for the tile under the cursor."""
    parts = [f"Position: ({cursor_x}, {cursor_y})"]

    if tile:
        parts.append(f"Terrain: {tile['terrain']}")
        parts.append(f"Biome: {tile['biome']}")
        parts.append(f"Elevation: {tile['elevation']:.2f}")
        if tile.get("entry_id"):
            parts.append(f"Source entry: #{tile['entry_id']}")

    if entity:
        parts.append(f"Entity: {entity['name']} ({entity['type']})")

    if not tile and not entity:
        parts.append("Unexplored territory")

    return "  |  ".join(parts)
