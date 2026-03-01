# -*- coding: utf-8 -*-
"""Interactive entities — NPC dialogue, shrine text, settlement trading."""
from __future__ import annotations

import json
import hashlib

from cyberjournal.world import world_db
from cyberjournal.world.economy import get_biome_resources, RESOURCE_VALUES
from cyberjournal.world.inventory import get_inventory, add_item, remove_item, has_item


# NPC greetings by personality
NPC_GREETINGS = {
    "cheerful": [
        "Well met, traveler! What a fine day!",
        "Hello there! Always good to see a new face!",
        "Greetings, friend! The world smiles upon us today!",
    ],
    "melancholic": [
        "Ah... another wanderer. The road is long, isn't it?",
        "Hello... I was just thinking about distant places.",
        "You look weary. I understand the feeling.",
    ],
    "nervous": [
        "Oh! You startled me. Who... who are you?",
        "S-stay back! Oh wait, you seem alright...",
        "Did you hear that? No? Just me then...",
    ],
    "adventurous": [
        "Ha! Another explorer! Where have you been?",
        "You look like someone who's seen the far reaches!",
        "Ready for adventure? There's always more to discover!",
    ],
    "wise": [
        "The journey matters more than the destination, traveler.",
        "I sense you seek something. Perhaps answers?",
        "Welcome. Sit, and let us share what we know.",
    ],
    "stoic": [
        "Traveler.",
        "You've come far.",
        "State your business.",
    ],
}

# Role-specific lore lines
ROLE_LORE = {
    "farmer": "The harvest this season has been {quality}. The soil tells its own story.",
    "shepherd": "My flock grows restless when the weather shifts. They sense things we cannot.",
    "woodcutter": "These trees have stood for ages. Each ring tells a year's tale.",
    "herbalist": "I've found rare specimens in the {biome}. Nature provides, if you know where to look.",
    "ranger": "I patrol these lands daily. Lately, I've noticed {observation}.",
    "hunter": "The game has been {quality} in these parts. The {biome} provides.",
    "explorer": "I've mapped regions beyond the {biome}. There's always more to see.",
    "botanist": "The flora here is remarkable. Some species I've never catalogued before.",
    "fisher": "The waters yield their bounty to those with patience.",
    "healer": "I tend to the sick and weary. Rest here if you need it.",
    "herder": "The livestock know these grasslands well. They lead, I follow.",
    "trader": "Business is {quality}. Always looking for new trade partners.",
    "miner": "Deep in the rock, there are treasures waiting to be found.",
    "nomad": "I don't stay in one place long. The {biome} calls me onward.",
    "prospector": "I've been surveying these peaks. The ore veins run deep.",
    "hermit": "Solitude suits me. But company is... not unwelcome.",
    "stonecutter": "Each stone has a purpose. You just have to find the right one.",
    "sentinel": "I watch over these lands. Nothing passes without my knowing.",
    "forester": "The forest is alive. Listen, and it speaks to you.",
    "mystic": "The threads of fate weave through this place. Can you feel it?",
    "rider": "My steed and I know every trail across the steppe.",
    "scout": "I've spotted unusual activity to the {direction}. Stay alert.",
    "scavenger": "One person's ruins are another's treasure. I find what others leave behind.",
    "outcast": "They cast me out, but I've found my own path.",
    "sailor": "The tides are changing. I can feel it in my bones.",
    "boatwright": "I build vessels to carry dreams across the water.",
}

QUALITIES = ["poor", "fair", "good", "excellent", "remarkable"]
OBSERVATIONS = ["more wildlife", "strange lights", "shifting terrain", "new growth"]
DIRECTIONS = ["north", "south", "east", "west"]


def build_npc_dialogue(props: dict, name: str, biome: str = "field") -> str:
    """Build dialogue text for an NPC based on their properties."""
    personality = props.get("personality", "stoic")
    role = props.get("role", "trader")

    # Pick greeting deterministically from name
    name_hash = int(hashlib.md5(name.encode()).hexdigest()[:8], 16)
    greetings = NPC_GREETINGS.get(personality, NPC_GREETINGS["stoic"])
    greeting = greetings[name_hash % len(greetings)]

    # Role lore
    lore_template = ROLE_LORE.get(role, "I go about my work here.")
    quality = QUALITIES[name_hash % len(QUALITIES)]
    observation = OBSERVATIONS[name_hash % len(OBSERVATIONS)]
    direction = DIRECTIONS[name_hash % len(DIRECTIONS)]
    lore = lore_template.format(
        quality=quality, biome=biome,
        observation=observation, direction=direction,
    )

    lines = [
        f"{name} ({role})",
        "=" * 30,
        "",
        f'"{greeting}"',
        "",
        f'"{lore}"',
    ]
    return "\n".join(lines)


def build_shrine_text(excerpt: str, biome: str) -> str:
    """Build atmospheric text for a shrine or ruin."""
    atmosphere = {
        "grassland": "Wind whispers through the open plains around this sacred place.",
        "woodland": "Ancient trees form a natural canopy over weathered stone.",
        "forest": "Moss and vines cling to carved surfaces half-hidden in shadow.",
        "desert": "Sand-worn glyphs catch the light on sun-bleached stone.",
        "alpine": "Thin mountain air carries echoes of forgotten prayers.",
        "marsh": "Mist curls around waterlogged ruins rising from the bog.",
    }
    atmo = atmosphere.get(biome, "A place of quiet contemplation.")

    lines = [
        "SHRINE",
        "=" * 30,
        "",
        atmo,
        "",
    ]
    if excerpt:
        lines.extend([
            "Inscribed text:",
            f'  "{excerpt}"',
        ])
    return "\n".join(lines)


async def get_settlement_trade_offer(entity_id: int, biome: str) -> dict[str, int]:
    """Get trade prices for a settlement. Returns {item: price_in_scraps}."""
    from cyberjournal.world.economy import get_settlement_production
    resources = await get_settlement_production(entity_id)
    if not resources:
        resources = get_biome_resources(biome)

    prices = {}
    for r in resources:
        base = RESOURCE_VALUES.get(r, 1)
        prices[r] = base
    return prices


async def execute_trade(item: str, qty: int, price: int, buying: bool) -> bool:
    """Execute a trade. Returns True if successful."""
    total_cost = price * qty
    if buying:
        # Player buys: spend scraps, gain item
        if not await has_item("scraps", total_cost):
            return False
        await remove_item("scraps", total_cost)
        await add_item(item, qty)
    else:
        # Player sells: spend item, gain scraps
        if not await has_item(item, qty):
            return False
        await remove_item(item, qty)
        await add_item("scraps", total_cost)
    return True
