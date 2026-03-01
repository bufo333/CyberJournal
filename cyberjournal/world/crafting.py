# -*- coding: utf-8 -*-
"""Building and crafting system — place structures, consume resources."""
from __future__ import annotations

import json

from cyberjournal.world import world_db
from cyberjournal.world.inventory import get_inventory, remove_item
from cyberjournal.world.player_stats import increment_stat

RECIPES = {
    "campfire": {
        "name": "Campfire",
        "ingredients": {"timber": 2},
        "entity_type": "structure",
        "symbol": "f",
        "description": "A warm campfire to rest by",
        "xp": 10,
    },
    "watchtower": {
        "name": "Watchtower",
        "ingredients": {"timber": 5, "stone": 3},
        "entity_type": "structure",
        "symbol": "T",
        "description": "A tall watchtower for surveying the land",
        "xp": 30,
    },
    "shrine_stone": {
        "name": "Stone Shrine",
        "ingredients": {"stone": 4, "herbs": 2},
        "entity_type": "structure",
        "symbol": "+",
        "description": "A hand-carved shrine for contemplation",
        "xp": 20,
    },
    "trade_post": {
        "name": "Trade Post",
        "ingredients": {"timber": 6, "scraps": 10},
        "entity_type": "structure",
        "symbol": "H",
        "description": "A trading outpost for commerce",
        "xp": 50,
    },
}


async def can_craft(recipe_name: str) -> tuple[bool, str]:
    """Check if the player can craft a recipe. Returns (can_craft, reason)."""
    recipe = RECIPES.get(recipe_name)
    if not recipe:
        return False, "Unknown recipe"

    inv = await get_inventory()
    for item, qty in recipe["ingredients"].items():
        if inv.get(item, 0) < qty:
            have = inv.get(item, 0)
            return False, f"Need {qty} {item} (have {have})"
    return True, "Ready to craft"


async def place_structure(recipe_name: str, x: int, y: int) -> int | None:
    """Craft and place a structure. Returns entity_id or None if failed."""
    recipe = RECIPES.get(recipe_name)
    if not recipe:
        return None

    ok, reason = await can_craft(recipe_name)
    if not ok:
        return None

    # Consume ingredients
    for item, qty in recipe["ingredients"].items():
        await remove_item(item, qty)

    # Insert entity
    props = json.dumps({
        "crafted": True,
        "recipe": recipe_name,
        "description": recipe["description"],
    })
    turn = await world_db.get_current_turn()
    entity_id = await world_db.insert_entity(
        x, y, recipe["entity_type"], recipe["name"],
        properties=props, turn=turn,
    )

    # Award XP
    await increment_stat("items_crafted")
    await increment_stat("structures_built")

    return entity_id


def format_recipes(inv: dict[str, int]) -> str:
    """Format recipes for display, showing which are craftable."""
    lines = ["CRAFTING RECIPES", "=" * 40, ""]
    for key, recipe in RECIPES.items():
        # Check ingredients
        can = True
        ing_parts = []
        for item, qty in recipe["ingredients"].items():
            have = inv.get(item, 0)
            marker = "+" if have >= qty else "-"
            ing_parts.append(f"{item} {have}/{qty} [{marker}]")
            if have < qty:
                can = False

        status = "[READY]" if can else "[MISSING]"
        lines.append(f"  {recipe['name']} {status}")
        lines.append(f"    {recipe['description']}")
        lines.append(f"    Requires: {', '.join(ing_parts)}")
        lines.append(f"    XP: {recipe['xp']}")
        lines.append("")
    return "\n".join(lines)
