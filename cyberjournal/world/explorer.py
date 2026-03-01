# -*- coding: utf-8 -*-
"""Exploration screen — arrow-key navigation of the world map."""
from __future__ import annotations

import json
import logging

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Footer, Header, Static

from cyberjournal.logic import load_config, get_entry
from cyberjournal.world import world_db
from cyberjournal.world.renderer import render_world_viewport, render_tile_info, build_minimap_overlay
from cyberjournal.world.quests import get_active_quests, complete_quest_at
from cyberjournal.world.weather import get_weather_at, WEATHER_SYMBOLS

logger = logging.getLogger(__name__)

VIEWPORT_W = 60
VIEWPORT_H = 20
DISCOVERY_RADIUS = 2


# ── Modal screens ──────────────────────────────────────────────


class InventoryScreen(ModalScreen):
    """Read-only inventory display."""

    BINDINGS = [Binding("escape", "dismiss", "Close"), Binding("i", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        with Container(id="modal-card"):
            yield Static("INVENTORY", classes="title")
            from textual.widgets import TextArea
            self.text_area = TextArea("Loading...", read_only=True, id="inv-text")
            yield self.text_area
        yield Footer()

    async def on_mount(self) -> None:
        from cyberjournal.world.inventory import get_inventory, format_inventory
        inv = await get_inventory()
        self.text_area.text = "INVENTORY\n" + "=" * 30 + "\n\n" + format_inventory(inv)


class StatsScreen(ModalScreen):
    """Player stats display."""

    BINDINGS = [Binding("escape", "dismiss", "Close"), Binding("s", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        with Container(id="modal-card"):
            yield Static("PLAYER STATS", classes="title")
            from textual.widgets import TextArea
            self.text_area = TextArea("Loading...", read_only=True, id="stats-text")
            yield self.text_area
        yield Footer()

    async def on_mount(self) -> None:
        from cyberjournal.world.player_stats import get_stats, format_stats
        stats = await get_stats()
        self.text_area.text = format_stats(stats)


class NPCDialogueScreen(ModalScreen):
    """NPC dialogue with optional trade action."""

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("t", "trade", "Trade"),
    ]

    def __init__(self, entity: dict, biome: str = "field") -> None:
        super().__init__()
        self.entity = entity
        self.biome = biome

    def compose(self) -> ComposeResult:
        with Container(id="modal-card"):
            yield Static("NPC DIALOGUE", classes="title")
            from textual.widgets import TextArea
            self.text_area = TextArea("Loading...", read_only=True, id="npc-text")
            yield self.text_area
        yield Footer()

    async def on_mount(self) -> None:
        from cyberjournal.world.interactions import build_npc_dialogue
        props = json.loads(self.entity["properties"]) if self.entity["properties"] else {}
        text = build_npc_dialogue(props, self.entity["name"], self.biome)

        # Check if near a settlement for trade
        ents = await world_db.get_entities_in_rect(
            self.entity["x"] - 3, self.entity["y"] - 3,
            self.entity["x"] + 3, self.entity["y"] + 3,
        )
        has_settlement = any(e["type"] == "settlement" for e in ents)
        if has_settlement:
            text += "\n\n[Press 't' to trade]"
        self._has_settlement = has_settlement
        self._nearby_ents = ents
        self.text_area.text = text

    async def action_trade(self) -> None:
        if not self._has_settlement:
            return
        settlements = [e for e in self._nearby_ents if e["type"] == "settlement"]
        if settlements:
            self.dismiss()
            self.app.push_screen(TradeScreen(dict(settlements[0]), self.biome))


class TradeScreen(ModalScreen):
    """Settlement trading screen."""

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("b", "buy", "Buy"),
        Binding("v", "sell", "Sell"),
    ]

    def __init__(self, settlement: dict, biome: str = "field") -> None:
        super().__init__()
        self.settlement = settlement
        self.biome = biome
        self._prices: dict[str, int] = {}
        self._selected_idx = 0
        self._items: list[str] = []
        self._mode = "buy"

    def compose(self) -> ComposeResult:
        with Container(id="modal-card"):
            yield Static("TRADING POST", classes="title")
            from textual.widgets import TextArea
            self.text_area = TextArea("Loading...", read_only=True, id="trade-text")
            yield self.text_area
        yield Footer()

    async def _refresh_trade(self) -> None:
        from cyberjournal.world.interactions import get_settlement_trade_offer
        from cyberjournal.world.inventory import get_inventory
        self._prices = await get_settlement_trade_offer(self.settlement["id"], self.biome)
        self._items = sorted(self._prices.keys())
        inv = await get_inventory()
        scraps = inv.get("scraps", 0)

        lines = [
            f"Trading at: {self.settlement['name']}",
            f"Your scraps: {scraps}",
            "",
            "AVAILABLE GOODS:",
            "",
        ]
        for item in self._items:
            price = self._prices[item]
            label = item.replace("_", " ").title()
            lines.append(f"  {label} — {price} scraps")
        lines.extend([
            "",
            "YOUR INVENTORY:",
            "",
        ])
        for item, qty in sorted(inv.items()):
            if qty > 0:
                label = item.replace("_", " ").title()
                sell_price = self._prices.get(item, 1)
                lines.append(f"  {label} x{qty} (sell: {sell_price} scraps)")
        lines.extend([
            "",
            "[b] Buy selected  [v] Sell selected  [Escape] Close",
        ])
        self.text_area.text = "\n".join(lines)

    async def on_mount(self) -> None:
        await self._refresh_trade()

    async def action_buy(self) -> None:
        if not self._items:
            return
        from cyberjournal.world.interactions import execute_trade
        from cyberjournal.world.player_stats import increment_stat
        item = self._items[self._selected_idx % len(self._items)]
        price = self._prices.get(item, 1)
        ok = await execute_trade(item, 1, price, buying=True)
        if ok:
            await increment_stat("items_traded")
            self._selected_idx = (self._selected_idx + 1) % max(1, len(self._items))
        await self._refresh_trade()

    async def action_sell(self) -> None:
        from cyberjournal.world.interactions import execute_trade
        from cyberjournal.world.inventory import get_inventory
        from cyberjournal.world.player_stats import increment_stat
        inv = await get_inventory()
        sellable = [(k, v) for k, v in inv.items() if v > 0 and k != "scraps"]
        if not sellable:
            return
        item, _ = sellable[0]
        price = self._prices.get(item, 1)
        ok = await execute_trade(item, 1, price, buying=False)
        if ok:
            await increment_stat("items_traded")
        await self._refresh_trade()


class LandmarkRestScreen(ModalScreen):
    """Shrine/ruin rest screen."""

    BINDINGS = [Binding("escape", "dismiss", "Close")]

    def __init__(self, entity: dict, biome: str = "field") -> None:
        super().__init__()
        self.entity = entity
        self.biome = biome

    def compose(self) -> ComposeResult:
        with Container(id="modal-card"):
            yield Static("LANDMARK", classes="title")
            from textual.widgets import TextArea
            self.text_area = TextArea("Loading...", read_only=True, id="rest-text")
            yield self.text_area
        yield Footer()

    async def on_mount(self) -> None:
        from cyberjournal.world.interactions import build_shrine_text
        excerpt = ""
        if self.entity["entry_id"] and hasattr(self.app, "session") and self.app.session:
            try:
                _, title, body = await get_entry(self.app.session, self.entity["entry_id"])
                excerpt = body[:200] + "..." if len(body) > 200 else body
            except Exception:
                pass
        self.text_area.text = build_shrine_text(excerpt, self.biome)


class CombatScreen(ModalScreen):
    """Combat encounter screen."""

    BINDINGS = [
        Binding("f", "fight", "Fight"),
        Binding("r", "run", "Run"),
        Binding("n", "negotiate", "Negotiate"),
        Binding("escape", "dismiss", "Close"),
    ]

    def __init__(self, enemy: dict, biome: str = "field") -> None:
        super().__init__()
        self.enemy = enemy
        self.biome = biome
        self._resolved = False

    def compose(self) -> ComposeResult:
        with Container(id="modal-card"):
            yield Static("ENCOUNTER!", classes="title")
            from textual.widgets import TextArea
            self.text_area = TextArea("Loading...", read_only=True, id="combat-text")
            yield self.text_area
        yield Footer()

    async def on_mount(self) -> None:
        lines = [
            "COMBAT ENCOUNTER",
            "=" * 30,
            "",
            f"  A {self.enemy['name']} appears!",
            f"  HP: {self.enemy['hp']}  ATK: {self.enemy['attack']}",
            "",
            f"  Biome: {self.biome}",
            "",
            "Actions:",
            "  [F] Fight    — Attack the enemy",
            "  [R] Run      — Try to flee",
            "  [N] Negotiate — Try to talk your way out",
        ]
        self.text_area.text = "\n".join(lines)

    async def _show_result(self, lines: list[str]) -> None:
        lines.append("")
        lines.append("[Escape] to continue")
        self.text_area.text = "\n".join(lines)

    async def action_fight(self) -> None:
        if self._resolved:
            return
        self._resolved = True
        from cyberjournal.world.combat import resolve_fight
        from cyberjournal.world.player_stats import get_stats, increment_stat
        from cyberjournal.world.inventory import add_item
        stats = await get_stats()
        result = resolve_fight(self.enemy, stats.get("level", 0))
        lines = ["COMBAT RESULT", "=" * 30, ""]
        if result["won"]:
            lines.append(f"  Victory! Defeated {self.enemy['name']}!")
            lines.append(f"  Damage taken: {result['hp_lost']}")
            if result["loot"]:
                lines.append(f"  Loot: {', '.join(result['loot'])}")
                for item in result["loot"]:
                    await add_item(item)
            await increment_stat("battles_won")
        else:
            lines.append(f"  Defeated by {self.enemy['name']}...")
            lines.append(f"  Damage taken: {result['hp_lost']}")
        await self._show_result(lines)

    async def action_run(self) -> None:
        if self._resolved:
            return
        self._resolved = True
        from cyberjournal.world.combat import resolve_flee
        from cyberjournal.world.player_stats import get_stats, increment_stat
        stats = await get_stats()
        result = resolve_flee(self.enemy, stats.get("level", 0))
        lines = ["FLEE ATTEMPT", "=" * 30, ""]
        if result["escaped"]:
            lines.append("  You escaped!")
            await increment_stat("battles_fled")
        else:
            lines.append("  Failed to escape!")
            lines.append(f"  Damage taken: {result['hp_lost']}")
        await self._show_result(lines)

    async def action_negotiate(self) -> None:
        if self._resolved:
            return
        self._resolved = True
        from cyberjournal.world.combat import resolve_negotiate
        from cyberjournal.world.player_stats import get_stats
        from cyberjournal.world.inventory import remove_item
        stats = await get_stats()
        result = resolve_negotiate(self.enemy, stats.get("level", 0))
        lines = ["NEGOTIATION", "=" * 30, ""]
        if result["succeeded"]:
            lines.append(f"  Negotiation succeeded!")
            if result["cost"] > 0:
                lines.append(f"  Cost: {result['cost']} scraps")
                await remove_item("scraps", result["cost"])
        else:
            lines.append("  Negotiation failed! The enemy attacks!")
        await self._show_result(lines)


class CraftingScreen(ModalScreen):
    """Crafting recipe selection screen."""

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("1", "craft_1", "Craft 1"),
        Binding("2", "craft_2", "Craft 2"),
        Binding("3", "craft_3", "Craft 3"),
        Binding("4", "craft_4", "Craft 4"),
    ]

    def __init__(self, on_select=None) -> None:
        super().__init__()
        self._on_select = on_select
        self._recipe_keys: list[str] = []

    def compose(self) -> ComposeResult:
        with Container(id="modal-card"):
            yield Static("CRAFTING", classes="title")
            from textual.widgets import TextArea
            self.text_area = TextArea("Loading...", read_only=True, id="craft-text")
            yield self.text_area
        yield Footer()

    async def on_mount(self) -> None:
        from cyberjournal.world.crafting import RECIPES, format_recipes
        from cyberjournal.world.inventory import get_inventory
        inv = await get_inventory()
        self._recipe_keys = list(RECIPES.keys())
        text = format_recipes(inv)
        text += "\n\nPress 1-4 to select a recipe for placement."
        self.text_area.text = text

    async def _try_craft(self, idx: int) -> None:
        from cyberjournal.world.crafting import RECIPES, can_craft
        if idx >= len(self._recipe_keys):
            return
        key = self._recipe_keys[idx]
        ok, reason = await can_craft(key)
        if not ok:
            self.text_area.text = f"Cannot craft: {reason}\n\n[Escape] to go back"
            return
        if self._on_select:
            self._on_select(key)
        self.dismiss()

    async def action_craft_1(self) -> None:
        await self._try_craft(0)

    async def action_craft_2(self) -> None:
        await self._try_craft(1)

    async def action_craft_3(self) -> None:
        await self._try_craft(2)

    async def action_craft_4(self) -> None:
        await self._try_craft(3)


# ── Main explorer screen ──────────────────────────────────────


class WorldExplorerScreen(Screen):
    """Full-screen world exploration with arrow-key cursor movement."""

    BINDINGS = [
        Binding("escape", "go_back", "Back"),
        Binding("up", "move_up", "Up", show=False),
        Binding("down", "move_down", "Down", show=False),
        Binding("left", "move_left", "Left", show=False),
        Binding("right", "move_right", "Right", show=False),
        Binding("enter", "inspect", "Inspect"),
        Binding("q", "show_quests", "Quests"),
        Binding("h", "show_history", "History"),
        Binding("i", "show_inventory", "Inventory"),
        Binding("s", "show_stats", "Stats"),
        Binding("m", "toggle_minimap", "Minimap"),
        Binding("c", "show_crafting", "Craft"),
    ]
    LAYERS = ("bg", "ui")

    def __init__(self) -> None:
        super().__init__()
        self.cursor_x = 0
        self.cursor_y = 0
        self.viewport_x = -VIEWPORT_W // 2
        self.viewport_y = -VIEWPORT_H // 2
        self.discovered: set[tuple[int, int]] = set()
        self.visited: set[tuple[int, int]] = set()
        self.show_minimap = False
        self.placing_structure: str | None = None

    def compose(self) -> ComposeResult:
        cfg = load_config()
        if cfg.get("ascii_art_enabled", True) and cfg.get("ascii_art"):
            yield Static(str(cfg.get("ascii_art")), id="ascii", classes="layer-bg", markup=False)

        yield Header(classes="layer-ui")

        with Container(id="modal-card", classes="layer-ui"):
            yield Static("WORLD EXPLORER", classes="title")
            self.map_display = Static("Loading world...", id="world-map", markup=True)
            yield self.map_display
            self.info_display = Static("", id="world-info", classes="meta")
            yield self.info_display

        yield Footer(classes="layer-ui")

    async def on_mount(self) -> None:
        # Load previously discovered positions
        raw = await world_db.get_meta("discovered_positions")
        if raw:
            self.discovered = {tuple(p) for p in json.loads(raw)}

        # Check if world is empty — rebuild from existing entries if needed
        await world_db.init_world_db()
        placements_raw = await world_db.get_meta("chunk_placements")
        if not placements_raw or placements_raw == "{}":
            if hasattr(self.app, "session") and self.app.session:
                try:
                    from cyberjournal.logic import rebuild_world
                    count = await rebuild_world(self.app.session)
                    if count > 0:
                        self.notify(f"World generated from {count} existing entries")
                except Exception:
                    logger.exception("Failed to rebuild world from entries")

        # Center cursor on the world content (first entry's chunk center)
        await self._center_on_world()
        await self._refresh_view()

    async def _center_on_world(self) -> None:
        """Move cursor to the center of the world's content."""
        from cyberjournal.world.grid import CHUNK_W, CHUNK_H
        placements_raw = await world_db.get_meta("chunk_placements")
        if not placements_raw or placements_raw == "{}":
            return
        placements = json.loads(placements_raw)
        if not placements:
            return
        first_key = min(placements.keys(), key=lambda k: int(k))
        cx, cy = placements[first_key]
        self.cursor_x = cx * CHUNK_W + CHUNK_W // 2
        self.cursor_y = cy * CHUNK_H + CHUNK_H // 2

    async def _on_move(self) -> None:
        """Handle post-movement logic: time, stats, combat."""
        from cyberjournal.world.daynight import advance_time
        from cyberjournal.world.player_stats import increment_stat
        from cyberjournal.world.combat import get_combat_state, save_combat_state, roll_encounter

        # Advance time
        await advance_time()

        # Track explored tiles
        pos = (self.cursor_x, self.cursor_y)
        if pos not in self.visited:
            self.visited.add(pos)
            await increment_stat("tiles_explored")

        # Combat encounter check
        combat_state = await get_combat_state()
        combat_state["move_count"] = combat_state.get("move_count", 0) + 1
        move_count = combat_state["move_count"]
        last_enc = combat_state.get("last_encounter_move", -10)

        if move_count - last_enc >= 10:
            tile = await world_db.get_tile(self.cursor_x, self.cursor_y)
            biome = tile["biome"] if tile else "grassland"
            enemy = roll_encounter(biome, self.cursor_x, self.cursor_y, move_count)
            if enemy:
                combat_state["last_encounter_move"] = move_count
                await save_combat_state(combat_state)
                await self.app.push_screen(CombatScreen(enemy, biome))
                return

        await save_combat_state(combat_state)

    async def _refresh_view(self) -> None:
        """Re-render the viewport around the cursor."""
        from cyberjournal.world.daynight import get_world_time, get_palette_for_time, format_time
        from cyberjournal.world.player_stats import get_stats, format_hud

        # Center viewport on cursor
        self.viewport_x = self.cursor_x - VIEWPORT_W // 2
        self.viewport_y = self.cursor_y - VIEWPORT_H // 2

        # Discover nearby hidden landmarks
        await self._check_discoveries()

        tiles = await world_db.get_tiles_in_rect(
            self.viewport_x, self.viewport_y,
            self.viewport_x + VIEWPORT_W - 1,
            self.viewport_y + VIEWPORT_H - 1,
        )
        entities = await world_db.get_entities_in_rect(
            self.viewport_x, self.viewport_y,
            self.viewport_x + VIEWPORT_W - 1,
            self.viewport_y + VIEWPORT_H - 1,
        )

        # Get time-based palette
        world_time = await get_world_time()
        palette = get_palette_for_time(world_time["hour"])

        # Build minimap overlay if active
        mm_overlay = None
        if self.show_minimap:
            all_tiles = await world_db.get_all_tiles_sampled(step=4)
            all_entities = await world_db.get_entities_in_rect(-9999, -9999, 9999, 9999)
            mm_overlay = build_minimap_overlay(
                all_tiles, all_entities, self.cursor_x, self.cursor_y, VIEWPORT_W,
            )

        map_text = render_world_viewport(
            tiles, entities,
            self.viewport_x, self.viewport_y,
            VIEWPORT_W, VIEWPORT_H,
            self.cursor_x, self.cursor_y,
            color=True,
            charset="utf",
            palette=palette,
            minimap_overlay=mm_overlay,
        )

        self.map_display.update(map_text)

        # Build info panel — start with HUD
        stats = await get_stats()
        hud = format_hud(stats)

        tile = await world_db.get_tile(self.cursor_x, self.cursor_y)
        tile_dict = None
        if tile:
            tile_dict = {
                "terrain": tile["terrain"],
                "biome": tile["biome"],
                "elevation": tile["elevation"],
                "entry_id": tile["entry_id"],
            }

        # Check for entity at cursor
        ents = await world_db.get_entities_in_rect(
            self.cursor_x, self.cursor_y,
            self.cursor_x, self.cursor_y,
        )
        entity_dict = None
        if ents:
            e = ents[0]
            entity_dict = {"name": e["name"], "type": e["type"]}

        info = render_tile_info(tile_dict, entity_dict, self.cursor_x, self.cursor_y)

        # Add weather info
        from cyberjournal.world.grid import CHUNK_W, CHUNK_H
        chunk_x = self.cursor_x // CHUNK_W
        chunk_y = self.cursor_y // CHUNK_H
        weather = await get_weather_at(chunk_x, chunk_y)
        weather_sym = WEATHER_SYMBOLS.get(weather, "~")
        info += f"  |  Weather: {weather} {weather_sym}"

        # Add time
        time_str = format_time(world_time)
        info += f"  |  {time_str}"

        # Placement mode indicator
        if self.placing_structure:
            info = f"PLACEMENT MODE: Enter to build, Escape to cancel ({self.placing_structure})\n" + info

        # Combine HUD + info
        full_info = hud + "\n" + info
        self.info_display.update(full_info)

        # Check quest completion
        completed = await complete_quest_at(self.cursor_x, self.cursor_y)
        if completed:
            from cyberjournal.world.player_stats import increment_stat
            await increment_stat("quests_completed")
            self.notify(f"Quest completed: {completed['title']}")

    async def _check_discoveries(self) -> None:
        """Reveal hidden landmarks when cursor is adjacent."""
        entities = await world_db.get_entities_in_rect(
            self.cursor_x - DISCOVERY_RADIUS,
            self.cursor_y - DISCOVERY_RADIUS,
            self.cursor_x + DISCOVERY_RADIUS,
            self.cursor_y + DISCOVERY_RADIUS,
        )

        for e in entities:
            pos = (e["x"], e["y"])
            if e["type"] == "hidden_landmark" and pos not in self.discovered:
                self.discovered.add(pos)
                import aiosqlite
                async with aiosqlite.connect(world_db.WORLD_DB_PATH) as conn:
                    await conn.execute(
                        "UPDATE world_entities SET type = 'landmark', "
                        "name = REPLACE(name, 'Hidden ', '') WHERE id = ?",
                        (e["id"],),
                    )
                    await conn.commit()

                disc_list = [list(p) for p in self.discovered]
                await world_db.set_meta("discovered_positions", json.dumps(disc_list))
                self.notify(f"Discovered: {e['name'].replace('Hidden ', '')}!")

                # Award discovery XP
                from cyberjournal.world.player_stats import increment_stat
                await increment_stat("discoveries_found")

                if e["entry_id"] and self.app.session:
                    try:
                        _, title, body = await get_entry(self.app.session, e["entry_id"])
                        excerpt = body[:150] + "..." if len(body) > 150 else body
                        self.notify(f"Ancient text found: \"{excerpt[:80]}...\"")
                    except Exception:
                        pass

    async def action_move_up(self) -> None:
        if self.placing_structure:
            self.cursor_y -= 1
            await self._refresh_view()
            return
        self.cursor_y -= 1
        await self._on_move()
        await self._refresh_view()

    async def action_move_down(self) -> None:
        if self.placing_structure:
            self.cursor_y += 1
            await self._refresh_view()
            return
        self.cursor_y += 1
        await self._on_move()
        await self._refresh_view()

    async def action_move_left(self) -> None:
        if self.placing_structure:
            self.cursor_x -= 1
            await self._refresh_view()
            return
        self.cursor_x -= 1
        await self._on_move()
        await self._refresh_view()

    async def action_move_right(self) -> None:
        if self.placing_structure:
            self.cursor_x += 1
            await self._refresh_view()
            return
        self.cursor_x += 1
        await self._on_move()
        await self._refresh_view()

    async def action_inspect(self) -> None:
        """Inspect entity or place structure."""
        # If in placement mode, place the structure
        if self.placing_structure:
            from cyberjournal.world.crafting import place_structure
            entity_id = await place_structure(
                self.placing_structure, self.cursor_x, self.cursor_y
            )
            if entity_id:
                self.notify(f"Built {self.placing_structure.replace('_', ' ').title()}!")
            else:
                self.notify("Failed to build structure")
            self.placing_structure = None
            await self._refresh_view()
            return

        # Check for entity at cursor
        ents = await world_db.get_entities_in_rect(
            self.cursor_x, self.cursor_y,
            self.cursor_x, self.cursor_y,
        )

        if ents:
            # Convert sqlite3.Row to dict so it's usable after connection closes
            e = dict(ents[0])
            tile = await world_db.get_tile(self.cursor_x, self.cursor_y)
            biome = tile["biome"] if tile else "field"

            if e["type"] == "npc":
                await self.app.push_screen(NPCDialogueScreen(e, biome))
                return
            elif e["type"] in ("shrine", "ruin", "landmark"):
                await self.app.push_screen(LandmarkRestScreen(e, biome))
                return
            elif e["type"] == "settlement":
                await self.app.push_screen(TradeScreen(e, biome))
                return

        # Fall back to entry inspection
        tile = await world_db.get_tile(self.cursor_x, self.cursor_y)
        if tile and tile["entry_id"] and self.app.session:
            try:
                created_at, title, body = await get_entry(self.app.session, tile["entry_id"])
                excerpt = body[:200] + "..." if len(body) > 200 else body
                self.info_display.update(
                    f"Entry: {title}\n{created_at}\n{excerpt}"
                )
            except Exception:
                self.info_display.update("Source entry not accessible")
        else:
            self.info_display.update("Nothing to inspect here")

    async def action_show_quests(self) -> None:
        """Show active quests."""
        quests = await get_active_quests()
        if not quests:
            self.info_display.update("No active quests.")
            return
        lines = ["ACTIVE QUESTS:"]
        for q in quests[:5]:
            lines.append(f"  [{q['type'].upper()}] {q['title']}")
            lines.append(f"    Target: ({q['target_x']}, {q['target_y']})")
        self.info_display.update("\n".join(lines))

    async def action_show_history(self) -> None:
        """Show recent world history."""
        await self.app.push_screen(WorldHistoryScreen())

    async def action_show_inventory(self) -> None:
        """Show inventory screen."""
        await self.app.push_screen(InventoryScreen())

    async def action_show_stats(self) -> None:
        """Show stats screen."""
        await self.app.push_screen(StatsScreen())

    async def action_toggle_minimap(self) -> None:
        """Toggle minimap overlay."""
        self.show_minimap = not self.show_minimap
        await self._refresh_view()

    async def action_show_crafting(self) -> None:
        """Show crafting screen."""
        def on_recipe_selected(recipe_name: str) -> None:
            self.placing_structure = recipe_name
            self.notify(f"Placement mode: move to location and press Enter")

        await self.app.push_screen(CraftingScreen(on_select=on_recipe_selected))

    async def on_screen_resume(self) -> None:
        """Restore focus when returning from modal screens."""
        await self._refresh_view()
        self.set_focus(self.map_display)

    async def action_go_back(self) -> None:
        if self.placing_structure:
            self.placing_structure = None
            await self._refresh_view()
            return
        self.dismiss()


class WorldHistoryScreen(Screen):
    """Scrollable chronological view of world events grouped by era."""

    BINDINGS = [
        Binding("escape", "go_back", "Back"),
    ]
    LAYERS = ("bg", "ui")

    def compose(self) -> ComposeResult:
        cfg = load_config()
        if cfg.get("ascii_art_enabled", True) and cfg.get("ascii_art"):
            yield Static(str(cfg.get("ascii_art")), id="ascii", classes="layer-bg", markup=False)

        yield Header(classes="layer-ui")

        with Container(id="modal-card", classes="layer-ui"):
            yield Static("WORLD HISTORY", classes="title")
            from textual.widgets import TextArea
            self.history_display = TextArea("Loading...", read_only=True, id="history-text")
            yield self.history_display

        yield Footer(classes="layer-ui")

    async def on_mount(self) -> None:
        from cyberjournal.world.timeline import get_world_timeline, get_era

        events = await get_world_timeline(limit=100)
        if not events:
            self.history_display.text = "No history yet. Create journal entries to build the world."
            return

        # Group by era
        eras: dict[str, list[dict]] = {}
        for ev in reversed(events):  # chronological order
            era = ev["era"]
            eras.setdefault(era, []).append(ev)

        lines = []
        for era_name, era_events in eras.items():
            lines.append(f"=== {era_name} ===")
            lines.append("")
            for ev in era_events:
                turn_str = f"Turn {ev['turn']}"
                loc = ""
                if ev["x"] is not None and ev["y"] is not None:
                    loc = f" at ({ev['x']}, {ev['y']})"
                entry_ref = ""
                if ev["entry_id"]:
                    entry_ref = f" [Entry #{ev['entry_id']}]"
                lines.append(f"  {turn_str}{loc}{entry_ref}")
                lines.append(f"    {ev['description']}")
                lines.append("")
            lines.append("")

        self.history_display.text = "\n".join(lines)

    async def action_go_back(self) -> None:
        self.dismiss()
