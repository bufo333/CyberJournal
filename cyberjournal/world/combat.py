# -*- coding: utf-8 -*-
"""Combat encounters — random enemies, fight/flee/negotiate mechanics."""
from __future__ import annotations

import hashlib
import json

from cyberjournal.world import world_db

# Encounter rates by biome (percentage chance per move)
ENCOUNTER_RATES = {
    "forest": 8,
    "temperate_forest": 8,
    "woodland": 7,
    "rainforest": 10,
    "desert": 7,
    "badlands": 12,
    "alpine": 6,
    "rocky_peaks": 9,
    "steppe": 5,
    "marsh": 8,
    "grassland": 4,
    "savanna": 6,
    "highland_forest": 7,
    "beach": 2,
    "riverbank": 3,
    "ocean": 1,
}

ENCOUNTER_COOLDOWN = 10  # minimum moves between encounters

# Enemy tables by biome
ENEMY_TABLE = {
    "forest": [
        {"name": "Wild Boar", "hp": 15, "attack": 3, "loot": ["hides", "game"]},
        {"name": "Forest Spider", "hp": 10, "attack": 4, "loot": []},
        {"name": "Bandit Scout", "hp": 20, "attack": 5, "loot": ["scraps"]},
    ],
    "temperate_forest": [
        {"name": "Timber Wolf", "hp": 18, "attack": 5, "loot": ["hides"]},
        {"name": "Bear", "hp": 30, "attack": 7, "loot": ["hides", "game"]},
    ],
    "woodland": [
        {"name": "Feral Cat", "hp": 8, "attack": 2, "loot": []},
        {"name": "Goblin Forager", "hp": 12, "attack": 3, "loot": ["scraps", "herbs"]},
    ],
    "rainforest": [
        {"name": "Giant Snake", "hp": 20, "attack": 6, "loot": ["hides"]},
        {"name": "Jungle Stalker", "hp": 25, "attack": 7, "loot": ["exotic_plants"]},
        {"name": "Venomous Frog", "hp": 8, "attack": 8, "loot": ["herbs"]},
    ],
    "desert": [
        {"name": "Sand Scorpion", "hp": 12, "attack": 5, "loot": []},
        {"name": "Desert Raider", "hp": 22, "attack": 6, "loot": ["scraps", "salt"]},
    ],
    "badlands": [
        {"name": "Rock Golem", "hp": 35, "attack": 8, "loot": ["stone", "ore"]},
        {"name": "Wasteland Prowler", "hp": 18, "attack": 6, "loot": ["scraps"]},
        {"name": "Dust Wraith", "hp": 15, "attack": 9, "loot": ["minerals"]},
    ],
    "alpine": [
        {"name": "Mountain Lion", "hp": 22, "attack": 6, "loot": ["hides"]},
        {"name": "Ice Elemental", "hp": 28, "attack": 7, "loot": ["gems"]},
    ],
    "rocky_peaks": [
        {"name": "Stone Gargoyle", "hp": 30, "attack": 8, "loot": ["stone"]},
        {"name": "Peak Wyvern", "hp": 40, "attack": 10, "loot": ["hides", "gems"]},
    ],
    "steppe": [
        {"name": "Prairie Wolf", "hp": 14, "attack": 4, "loot": ["hides"]},
        {"name": "Nomad Raider", "hp": 20, "attack": 5, "loot": ["scraps", "wool"]},
    ],
    "marsh": [
        {"name": "Bog Creature", "hp": 18, "attack": 5, "loot": ["peat"]},
        {"name": "Swamp Leech", "hp": 10, "attack": 3, "loot": ["herbs"]},
    ],
    "grassland": [
        {"name": "Wild Dog Pack", "hp": 12, "attack": 3, "loot": []},
        {"name": "Highwayman", "hp": 16, "attack": 4, "loot": ["scraps"]},
    ],
    "savanna": [
        {"name": "Hyena Pack", "hp": 16, "attack": 5, "loot": ["hides"]},
        {"name": "Lion", "hp": 28, "attack": 7, "loot": ["hides"]},
    ],
    "highland_forest": [
        {"name": "Forest Troll", "hp": 32, "attack": 6, "loot": ["timber", "mushrooms"]},
        {"name": "Mountain Bear", "hp": 35, "attack": 8, "loot": ["hides", "game"]},
    ],
}

# Default enemies for biomes not in the table
DEFAULT_ENEMIES = [
    {"name": "Wandering Beast", "hp": 15, "attack": 4, "loot": ["scraps"]},
    {"name": "Shadow Creature", "hp": 12, "attack": 5, "loot": []},
]


def roll_encounter(biome: str, cursor_x: int, cursor_y: int, move_count: int) -> dict | None:
    """Roll for a random encounter. Returns enemy dict or None."""
    rate = ENCOUNTER_RATES.get(biome, 3)
    # Deterministic roll from position + move count
    seed_str = f"encounter_{cursor_x}_{cursor_y}_{move_count}"
    roll = int(hashlib.md5(seed_str.encode()).hexdigest()[:4], 16) % 100
    if roll >= rate:
        return None

    enemies = ENEMY_TABLE.get(biome, DEFAULT_ENEMIES)
    idx = int(hashlib.md5(seed_str.encode()).hexdigest()[4:8], 16) % len(enemies)
    return enemies[idx].copy()


def resolve_fight(enemy: dict, player_level: int) -> dict:
    """Resolve a fight. Returns {won, hp_lost, loot}."""
    # Player power scales with level
    player_attack = 5 + player_level * 2
    player_hp = 20 + player_level * 5

    enemy_hp = enemy["hp"]
    enemy_atk = enemy["attack"]

    # Simple combat simulation
    rounds = 0
    damage_taken = 0
    while enemy_hp > 0 and player_hp > 0:
        # Player attacks first
        enemy_hp -= player_attack
        if enemy_hp <= 0:
            break
        # Enemy attacks
        player_hp -= enemy_atk
        damage_taken += enemy_atk
        rounds += 1

    won = player_hp > 0
    return {
        "won": won,
        "hp_lost": damage_taken,
        "loot": enemy.get("loot", []) if won else [],
        "rounds": rounds,
    }


def resolve_flee(enemy: dict, player_level: int) -> dict:
    """Resolve a flee attempt. Returns {escaped, hp_lost}."""
    # Higher level = better chance to flee
    flee_chance = min(85, 50 + player_level * 5)
    # Use enemy attack as a simple check
    seed = enemy["hp"] + enemy["attack"] + player_level
    roll = (seed * 7 + 13) % 100
    escaped = roll < flee_chance
    hp_lost = enemy["attack"] if not escaped else max(0, enemy["attack"] // 2)
    return {"escaped": escaped, "hp_lost": hp_lost}


def resolve_negotiate(enemy: dict, player_level: int) -> dict:
    """Resolve a negotiation attempt. Returns {succeeded, cost}."""
    # Some enemies can't be negotiated with
    negotiate_chance = min(70, 30 + player_level * 5)
    seed = enemy["hp"] * 3 + enemy["attack"] * 7 + player_level
    roll = (seed * 11 + 17) % 100
    succeeded = roll < negotiate_chance
    cost = max(1, enemy["attack"] // 2) if succeeded else 0
    return {"succeeded": succeeded, "cost": cost}


async def get_combat_state() -> dict:
    """Get persistent combat state (move counter, cooldown)."""
    raw = await world_db.get_meta("combat_state")
    if raw:
        return json.loads(raw)
    return {"move_count": 0, "last_encounter_move": -ENCOUNTER_COOLDOWN}


async def save_combat_state(state: dict) -> None:
    """Save combat state."""
    await world_db.set_meta("combat_state", json.dumps(state))
