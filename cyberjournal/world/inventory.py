# -*- coding: utf-8 -*-
"""Inventory system — item storage and management for the world explorer."""
from __future__ import annotations

import json

from cyberjournal.world import world_db


async def get_inventory() -> dict[str, int]:
    """Get the player's current inventory."""
    raw = await world_db.get_meta("player_inventory")
    if raw:
        return json.loads(raw)
    return {}


async def set_inventory(inv: dict[str, int]) -> None:
    """Persist inventory, removing zero-quantity items."""
    cleaned = {k: v for k, v in inv.items() if v > 0}
    await world_db.set_meta("player_inventory", json.dumps(cleaned))


async def add_item(name: str, qty: int = 1) -> dict[str, int]:
    """Add items to inventory. Returns updated inventory."""
    inv = await get_inventory()
    inv[name] = inv.get(name, 0) + qty
    await set_inventory(inv)
    return inv


async def remove_item(name: str, qty: int = 1) -> bool:
    """Remove items from inventory. Returns False if insufficient."""
    inv = await get_inventory()
    if inv.get(name, 0) < qty:
        return False
    inv[name] = inv.get(name, 0) - qty
    await set_inventory(inv)
    return True


async def has_item(name: str, qty: int = 1) -> bool:
    """Check if player has at least qty of an item."""
    inv = await get_inventory()
    return inv.get(name, 0) >= qty


async def get_item_catalog() -> dict[str, dict]:
    """Get the item catalog with metadata."""
    raw = await world_db.get_meta("item_catalog")
    if raw:
        return json.loads(raw)
    return DEFAULT_CATALOG.copy()


DEFAULT_CATALOG = {
    # Resources
    "timber": {"type": "resource", "desc": "Sturdy wood planks"},
    "stone": {"type": "resource", "desc": "Hewn stone blocks"},
    "herbs": {"type": "resource", "desc": "Medicinal herbs"},
    "ore": {"type": "resource", "desc": "Raw metal ore"},
    "grain": {"type": "resource", "desc": "Harvested grain"},
    "fish": {"type": "resource", "desc": "Fresh catch"},
    "clay": {"type": "resource", "desc": "Moldable clay"},
    "gems": {"type": "resource", "desc": "Precious gemstones"},
    "hides": {"type": "resource", "desc": "Animal hides"},
    "salt": {"type": "resource", "desc": "Mineral salt"},
    "peat": {"type": "resource", "desc": "Dried peat fuel"},
    "shells": {"type": "resource", "desc": "Sea shells"},
    "wool": {"type": "resource", "desc": "Soft wool fibers"},
    "mushrooms": {"type": "resource", "desc": "Foraged mushrooms"},
    "minerals": {"type": "resource", "desc": "Assorted minerals"},
    "exotic_plants": {"type": "resource", "desc": "Rare botanical specimens"},
    "horses": {"type": "resource", "desc": "Tamed horses"},
    "livestock": {"type": "resource", "desc": "Farm animals"},
    "game": {"type": "resource", "desc": "Hunted game meat"},
    # Currency
    "scraps": {"type": "currency", "desc": "Tradeable scrap pieces"},
    # Weapons
    "iron_sword": {"type": "weapon", "desc": "A sturdy iron blade"},
    "wooden_club": {"type": "weapon", "desc": "A crude wooden club"},
    "stone_axe": {"type": "weapon", "desc": "A sharp stone axe"},
    # Consumables
    "health_potion": {"type": "consumable", "desc": "Restores health"},
    "trail_rations": {"type": "consumable", "desc": "Sustaining travel food"},
}


def format_inventory(inv: dict[str, int], catalog: dict[str, dict] | None = None) -> str:
    """Format inventory for display."""
    if not inv:
        return "  (empty)"
    cat = catalog or DEFAULT_CATALOG
    lines = []
    # Group by type
    grouped: dict[str, list[tuple[str, int]]] = {}
    for name, qty in sorted(inv.items()):
        item_type = cat.get(name, {}).get("type", "misc")
        grouped.setdefault(item_type, []).append((name, qty))
    for item_type, items in sorted(grouped.items()):
        lines.append(f"  [{item_type.upper()}]")
        for name, qty in items:
            desc = cat.get(name, {}).get("desc", "")
            label = name.replace("_", " ").title()
            lines.append(f"    {label} x{qty}  {desc}")
    return "\n".join(lines)
