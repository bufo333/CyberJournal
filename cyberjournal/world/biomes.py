# -*- coding: utf-8 -*-
"""Biome classification and entity derivation from entry content."""
from __future__ import annotations


# Map (terrain, elevation_range, moisture_range) → biome
BIOME_TABLE = {
    "water": "ocean",
    "shore": "beach",
    "river": "riverbank",
    "road": "road",
    "poi": "landmark",
}


def classify_biome(terrain: str, elevation: float, moisture: float) -> str:
    """Map terrain type + elevation + moisture to a biome name."""
    if terrain in BIOME_TABLE:
        return BIOME_TABLE[terrain]

    if terrain == "mount":
        return "alpine" if moisture > 0.5 else "rocky_peaks"

    if terrain == "hill":
        if moisture > 0.6:
            return "highland_forest"
        elif moisture > 0.3:
            return "steppe"
        else:
            return "badlands"

    if terrain == "forest":
        if moisture > 0.75:
            return "rainforest"
        elif elevation > 0.6:
            return "temperate_forest"
        else:
            return "woodland"

    # field / default
    if moisture > 0.7:
        return "marsh"
    elif moisture > 0.5:
        return "grassland"
    elif moisture > 0.3:
        return "savanna"
    else:
        return "desert"


# Entity types derived from entry metadata
ENTITY_TYPES = {
    "settlement": {"symbol": "H", "description": "A settlement built from journal words"},
    "landmark": {"symbol": "!", "description": "A notable landmark"},
    "resource": {"symbol": "$", "description": "A resource deposit"},
    "ruin": {"symbol": "%", "description": "Ancient ruins"},
    "shrine": {"symbol": "+", "description": "A contemplation shrine"},
    "npc": {"symbol": "P", "description": "A person of the world"},
    "hidden_landmark": {"symbol": "?", "description": "Something hidden nearby..."},
    "structure": {"symbol": "#", "description": "A player-built structure"},
}


def derive_entity_type(word_count: int, mood: str, biome: str) -> str:
    """Determine what kind of entity to place based on entry metadata."""
    if word_count > 200:
        return "settlement"
    if mood in ("sad", "anxious"):
        return "ruin"
    if mood in ("calm", "neutral"):
        return "shrine"
    if biome in ("forest", "rainforest", "woodland"):
        return "resource"
    return "landmark"
