# -*- coding: utf-8 -*-
"""Tile-based world renderer for the Textual TUI.

Renders a viewport of the world map with cursor, using the existing PALETTE colors.
"""
from __future__ import annotations

from cyberjournal.map import PALETTE, SYMBOLS_UTF, SYMBOLS_ASCII, _fg, ANSI_RESET
from cyberjournal.world.biomes import ENTITY_TYPES

# Map ANSI color codes to Rich color names for Textual markup
_ANSI_TO_RICH = {
    "31": "red", "32": "green", "33": "yellow", "34": "blue",
    "35": "magenta", "36": "cyan", "37": "white",
    "90": "bright_black", "91": "bright_red", "92": "bright_green",
    "93": "bright_yellow", "94": "bright_blue", "95": "bright_magenta",
    "96": "bright_cyan", "97": "bright_white",
}


def _rich_fg(code: str, ch: str) -> str:
    """Wrap a character in Rich markup color tags.

    Handles compound ANSI codes like '32;1' (color + bold).
    """
    escaped = ch.replace("[", "\\[")
    parts = code.split(";")
    color_code = parts[0]
    bold = "1" in parts[1:]
    color_name = _ANSI_TO_RICH.get(color_code)
    if not color_name:
        return escaped
    if bold:
        return f"[bold {color_name}]{escaped}[/bold {color_name}]"
    return f"[{color_name}]{escaped}[/{color_name}]"


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


def build_minimap_overlay(
    tiles: list,
    entities: list,
    cursor_x: int,
    cursor_y: int,
    viewport_w: int,
    width: int = 16,
    height: int = 8,
) -> dict[tuple[int, int], str]:
    """Build a minimap as a dict of (viewport_col, viewport_row) -> char.

    Positioned in the top-right corner of the viewport.
    Returns empty dict if no tiles.
    """
    if not tiles:
        return {}

    # Find world bounding box
    all_x = [t["x"] for t in tiles]
    all_y = [t["y"] for t in tiles]
    min_x, max_x = min(all_x), max(all_x)
    min_y, max_y = min(all_y), max(all_y)
    world_w = max(1, max_x - min_x + 1)
    world_h = max(1, max_y - min_y + 1)

    sx = max(1, world_w // width)
    sy = max(1, world_h // height)

    grid: dict[tuple[int, int], str] = {}
    for t in tiles:
        gx = (t["x"] - min_x) // sx
        gy = (t["y"] - min_y) // sy
        if 0 <= gx < width and 0 <= gy < height:
            grid[(gx, gy)] = SYMBOLS_UTF.get(t["terrain"], ".")

    for e in entities:
        gx = (e["x"] - min_x) // sx
        gy = (e["y"] - min_y) // sy
        if 0 <= gx < width and 0 <= gy < height:
            grid[(gx, gy)] = ENTITY_TYPES.get(e["type"], {}).get("symbol", "?")

    px = (cursor_x - min_x) // sx
    py = (cursor_y - min_y) // sy

    # Build minimap lines (with border)
    box_w = width + 2  # border chars
    box_h = height + 2
    start_col = viewport_w - box_w - 1

    if start_col < 0:
        return {}

    overlay: dict[tuple[int, int], str] = {}

    # Top border
    overlay[(start_col, 0)] = "\u250c"
    for i in range(width):
        overlay[(start_col + 1 + i, 0)] = "\u2500"
    overlay[(start_col + width + 1, 0)] = "\u2510"

    # Content rows
    for y in range(height):
        row_y = y + 1
        overlay[(start_col, row_y)] = "\u2502"
        for x in range(width):
            if x == px and y == py:
                overlay[(start_col + 1 + x, row_y)] = "@"
            elif (x, y) in grid:
                overlay[(start_col + 1 + x, row_y)] = grid[(x, y)]
            else:
                overlay[(start_col + 1 + x, row_y)] = " "
        overlay[(start_col + width + 1, row_y)] = "\u2502"

    # Bottom border
    bot_y = height + 1
    overlay[(start_col, bot_y)] = "\u2514"
    for i in range(width):
        overlay[(start_col + 1 + i, bot_y)] = "\u2500"
    overlay[(start_col + width + 1, bot_y)] = "\u2518"

    return overlay


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
    palette: dict | None = None,
    minimap_overlay: dict[tuple[int, int], str] | None = None,
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

    # Track if the world has any content at all
    has_content = bool(tile_map or entity_map)

    lines = []
    for dy in range(viewport_h):
        wy = viewport_y + dy
        row_chars = []
        for dx in range(viewport_w):
            wx = viewport_x + dx
            is_cursor = (wx == cursor_x and wy == cursor_y)
            # Crosshair: show markers on cursor row/col
            is_crosshair = (wx == cursor_x or wy == cursor_y)

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

            # Minimap overlay takes priority (except for cursor)
            if not is_cursor and minimap_overlay and (dx, dy) in minimap_overlay:
                mm_ch = minimap_overlay[(dx, dy)]
                if color:
                    row_chars.append(_rich_fg("97", mm_ch))  # bright white
                else:
                    row_chars.append(mm_ch)
                continue

            if is_cursor:
                if color:
                    row_chars.append("[reverse]@[/reverse]")
                else:
                    row_chars.append("@")
            elif is_crosshair and tile_type == "unknown":
                # Show faint crosshair on empty tiles to help locate cursor
                if wx == cursor_x:
                    row_chars.append("|")
                else:
                    row_chars.append("-")
            elif color and tile_type != "unknown":
                pal = palette or PALETTE
                code = pal.get(tile_type, pal.get("unknown", "37"))
                row_chars.append(_rich_fg(code, ch))
            else:
                if color:
                    row_chars.append(ch.replace("[", "\\["))
                else:
                    row_chars.append(ch)

        lines.append("".join(row_chars))

    if not has_content:
        # Overlay a help message in the center of the viewport
        msg = "\\[ Empty world — create journal entries to generate terrain ]"
        center_y = viewport_h // 2 + 2
        if center_y < len(lines):
            line = lines[center_y]
            start = max(0, (viewport_w - len(msg)) // 2)
            lines[center_y] = line[:start] + msg + line[start + len(msg):]

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
