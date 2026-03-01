# -*- coding: utf-8 -*-
"""Exploration screen — arrow-key navigation of the world map."""
from __future__ import annotations

import json
import logging

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

from cyberjournal.logic import load_config, get_entry
from cyberjournal.world import world_db
from cyberjournal.world.renderer import render_world_viewport, render_tile_info
from cyberjournal.world.quests import get_active_quests, complete_quest_at
from cyberjournal.world.weather import get_weather_at, WEATHER_SYMBOLS

logger = logging.getLogger(__name__)

VIEWPORT_W = 60
VIEWPORT_H = 20
DISCOVERY_RADIUS = 2


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
    ]
    LAYERS = ("bg", "ui")

    def __init__(self) -> None:
        super().__init__()
        self.cursor_x = 0
        self.cursor_y = 0
        self.viewport_x = -VIEWPORT_W // 2
        self.viewport_y = -VIEWPORT_H // 2
        self.discovered: set[tuple[int, int]] = set()

    def compose(self) -> ComposeResult:
        cfg = load_config()
        if cfg.get("ascii_art_enabled", True) and cfg.get("ascii_art"):
            yield Static(str(cfg.get("ascii_art")), id="ascii", classes="layer-bg", markup=False)

        yield Header(classes="layer-ui")

        with Container(id="modal-card", classes="layer-ui"):
            yield Static("WORLD EXPLORER", classes="title")
            self.map_display = Static("Loading world...", id="world-map", markup=False)
            yield self.map_display
            self.info_display = Static("", id="world-info", classes="meta")
            yield self.info_display

        yield Footer(classes="layer-ui")

    async def on_mount(self) -> None:
        # Load previously discovered positions
        raw = await world_db.get_meta("discovered_positions")
        if raw:
            self.discovered = {tuple(p) for p in json.loads(raw)}
        await self._refresh_view()

    async def _refresh_view(self) -> None:
        """Re-render the viewport around the cursor."""
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

        map_text = render_world_viewport(
            tiles, entities,
            self.viewport_x, self.viewport_y,
            VIEWPORT_W, VIEWPORT_H,
            self.cursor_x, self.cursor_y,
            color=False,
            charset="utf",
        )
        self.map_display.update(map_text)

        # Build info panel
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

        self.info_display.update(info)

        # Check quest completion
        completed = await complete_quest_at(self.cursor_x, self.cursor_y)
        if completed:
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
                # Reveal the hidden landmark — change its type to landmark
                import aiosqlite
                async with aiosqlite.connect(world_db.WORLD_DB_PATH) as conn:
                    await conn.execute(
                        "UPDATE world_entities SET type = 'landmark', "
                        "name = REPLACE(name, 'Hidden ', '') WHERE id = ?",
                        (e["id"],),
                    )
                    await conn.commit()

                # Persist discovered positions
                disc_list = [list(p) for p in self.discovered]
                await world_db.set_meta("discovered_positions", json.dumps(disc_list))

                # Show discovery notification
                self.notify(f"Discovered: {e['name'].replace('Hidden ', '')}!")

                # Check if it's an "ancient text" — show entry excerpt
                if e["entry_id"] and self.app.session:
                    try:
                        _, title, body = await get_entry(self.app.session, e["entry_id"])
                        excerpt = body[:150] + "..." if len(body) > 150 else body
                        self.notify(f"Ancient text found: \"{excerpt[:80]}...\"")
                    except Exception:
                        pass

    async def action_move_up(self) -> None:
        self.cursor_y -= 1
        await self._refresh_view()

    async def action_move_down(self) -> None:
        self.cursor_y += 1
        await self._refresh_view()

    async def action_move_left(self) -> None:
        self.cursor_x -= 1
        await self._refresh_view()

    async def action_move_right(self) -> None:
        self.cursor_x += 1
        await self._refresh_view()

    async def action_inspect(self) -> None:
        """Show details for the tile under cursor."""
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

    async def on_screen_resume(self) -> None:
        """Restore focus when returning from history screen."""
        await self._refresh_view()
        self.set_focus(self.map_display)

    async def action_go_back(self) -> None:
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
