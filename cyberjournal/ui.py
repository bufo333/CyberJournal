# -*- coding: utf-8 -*-
"""Textual UI for CyberJournal.

This file contains ONLY the UI: screens, modals, and the App wrapper.
It does not alter backend names or logic. It expects the backend to expose:
    - load_config(), save_config()
    - init_db(), register_user(), login_user()
    - add_entry(), list_entries(), get_entry(), search_entries()
    - SessionKeys (dataclass)

Theme switching:
    We use a single theme.css with 3 variants (vt220/amber/neon) implemented
    as CSS class scopes: `.theme-vt220`, `.theme-amber`, `.theme-neon`.
    The app toggles one of these classes at runtime based on the saved config.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    Static,
    TabPane,
    TabbedContent,
    TextArea,
)

# Import the existing backend surface WITHOUT renaming
from cyberjournal.logic import (  # type: ignore
    SessionKeys,
    load_config,
    save_config,
    init_db,
    register_user,
    login_user,
    add_entry,
    list_entries,
    get_entry,
    search_entries,
    update_entry,
    delete_entry,
)

THEME_CSS_PATH = str(Path(__file__).with_name("theme.css"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _apply_app_theme(app: App, theme_key: str) -> None:
    """Apply one of ('vt220_green', 'as400_amber', 'vector_neon') to the App.

    We attach exactly one of the classes: theme-vt220 / theme-amber / theme-neon.
    """
    valid = {
        "vt220_green": "theme-vt220",
        "as400_amber": "theme-amber",
        "vector_neon": "theme-neon",
    }
    target = valid.get(theme_key, "theme-vt220")
    # Remove all three, then add target
    for cls in ("theme-vt220", "theme-amber", "theme-neon"):
        app.set_class(False, cls)
    app.set_class(True, target)


# ---------------------------------------------------------------------------
# Modals
# ---------------------------------------------------------------------------

class SettingsModal(ModalScreen[None]):
    """Settings: theme choice + ASCII background text. Persisted to config."""

    def compose(self) -> ComposeResult:
        cfg = load_config()
        active = str(cfg.get("active_theme", "vt220_green"))
        ascii_enabled = bool(cfg.get("ascii_art_enabled", True))
        ascii_text = str(cfg.get("ascii_art", ""))

        yield Container(
            Static("SETTINGS", classes="title"),
            Horizontal(
                Button("VT220 GREEN", id="t_green", classes="-primary" if active == "vt220_green" else ""),
                Button("AS/400 AMBER", id="t_amber", classes="-primary" if active == "as400_amber" else ""),
                Button("VECTOR NEON", id="t_neon", classes="-primary" if active == "vector_neon" else ""),
                id="theme-row",
            ),
            Static("ASCII ART (background behind centered window)", classes="hint"),
            Input(value="on" if ascii_enabled else "off", id="ascii_toggle"),
            TextArea( id="ascii_text", placeholder="ASCII art here..."),
            Horizontal(Button("Save", id="save", classes="-primary"), Button("Close", id="close")),
            id="modal-card",
            classes="layer-ui",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        cfg = load_config()
        bid = event.button.id or ""
        if bid == "t_green":
            cfg["active_theme"] = "vt220_green"
        elif bid == "t_amber":
            cfg["active_theme"] = "as400_amber"
        elif bid == "t_neon":
            cfg["active_theme"] = "vector_neon"
        elif bid == "save":
            ascii_toggle = self.query_one("#ascii_toggle", Input).value.strip().lower()
            ascii_text = self.query_one("#ascii_text", TextArea).text
            cfg["ascii_art_enabled"] = ascii_toggle in {"1", "true", "on", "yes", "y"}
            cfg["ascii_art"] = ascii_text
            save_config(cfg)
            # Apply theme immediately
            _apply_app_theme(self.app, str(cfg.get("active_theme", "vt220_green")))
            self.app.notify("Settings saved.")
            self.app.pop_screen()
            return
        elif bid == "close":
            self.app.pop_screen()
            return

        # Persist theme choice and refresh modal buttons' highlight state
        save_config(cfg)
        _apply_app_theme(self.app, str(cfg.get("active_theme", "vt220_green")))
        self.app.pop_screen()
        self.app.push_screen(SettingsModal())


class CreateUserModal(ModalScreen[None]):
    """New user registration overlay."""

    def compose(self) -> ComposeResult:
        yield Container(
            Static("CREATE USER", classes="title"),
            Input(placeholder="username", id="u"),
            Input(placeholder="password", password=True, id="p"),
            Input(placeholder="confirm", password=True, id="c"),
            Horizontal(Button("Create", id="create", classes="-primary"), Button("Close", id="close")),
            id="modal-card",
            classes="layer-ui",
        )

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "create":
            u = self.query_one("#u", Input).value.strip()
            p = self.query_one("#p", Input).value
            c = self.query_one("#c", Input).value
            if not u or not p or p != c:
                self.app.notify("Invalid username/password")
                return
            try:
                await register_user(u, p)
                self.app.notify("User created.")
                self.app.pop_screen()
            except Exception as exc:
                self.app.notify(str(exc))
        elif event.button.id == "close":
            self.app.pop_screen()


class ResetPasswordModal(ModalScreen[None]):
    """Password reset placeholder (UI only)."""

    def compose(self) -> ComposeResult:
        yield Container(
            Static("RESET PASSWORD", classes="title"),
            Input(placeholder="username", id="u"),
            Input(placeholder="current password", password=True, id="p0"),
            Input(placeholder="new password", password=True, id="p1"),
            Input(placeholder="confirm new", password=True, id="p2"),
            Horizontal(Button("Reset", id="reset", classes="-primary"), Button("Close", id="close")),
            id="modal-card",
            classes="layer-ui",
        )

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "reset":
            self.app.notify("Password reset not implemented yet.")
            self.app.pop_screen()
        elif event.button.id == "close":
            self.app.pop_screen()

class EditEntryModal(ModalScreen[None]):
    """Modal to edit an existing journal entry."""
    def __init__(self, entry_id: int):
        super().__init__()
        self.entry_id = entry_id

    def compose(self) -> ComposeResult:

        yield Container(
            Static("EDIT ENTRY", classes="title"),
            Input(placeholder="title", id="edit_title"),
            TextArea(id="edit_body", placeholder="body text", show_cursor=True),
            Horizontal(Button("Save", id="save", classes="-primary"), Button("Cancel", id="cancel")),
            id="modal-card",
            classes="layer-ui",
        )
        # After creating an Input
        self.title_in = Input(placeholder="title")
        # Make the caret more visible if supported
        if hasattr(self.title_in, "cursor_blink"):
            self.title_in.cursor_blink = True
        if hasattr(self.title_in, "cursor_style"):
            self.title_in.cursor_style = "block"  # "line" / "block" (supported in newer Textual)

        # After creating a TextArea
        self.body_in = TextArea(placeholder="body (Ctrl+Enter to save)")
        if hasattr(self.body_in, "cursor_blink"):
            self.body_in.cursor_blink = True
        if hasattr(self.body_in, "cursor_style"):
            self.body_in.cursor_style = "block"

    async def on_mount(self) -> None:
        created_at, title, body = await get_entry(self.app.session, self.entry_id)
        self.query_one("#edit_title", Input).value = title
        self.query_one("#edit_body", TextArea).text = body

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "save":
            title = self.query_one("#edit_title", Input).value.strip()
            body = self.query_one("#edit_body", TextArea).text
            await update_entry(self.app.session, self.entry_id, title, body)
            self.app.notify("Entry updated.")
            self.app.pop_screen()      # close modal
            # Notify parent to refresh its list if needed
            if hasattr(self.app.screen_stack[-1], "refresh_list"):
                await self.app.screen_stack[-1].refresh_list()
        elif bid == "cancel":
            self.app.pop_screen()

# ---------------------------------------------------------------------------
# Screens
# ---------------------------------------------------------------------------

class LoginScreen(Screen):
    """Login screen. After success, it is fully replaced by JournalHomeScreen.

    ESC from here quits the app (requested behavior).
    """

    BINDINGS = [Binding("escape", "app.quit", "Quit")]
    LAYERS = ("bg", "ui")

    def compose(self) -> ComposeResult:
        cfg = load_config()
        # Background frame lives on its own layer, so the modal is centered above
        if cfg.get("ascii_art_enabled", True) and cfg.get("ascii_art"):
            yield Static(str(cfg.get("ascii_art")), id="ascii", markup=False)
        yield Header()
        # Centered modal card on its own "ui" layer
        yield Container(
            Static("LOGIN", classes="title"),
            Input(placeholder="username", id="username"),
            Input(placeholder="password", password=True, id="password"),
            Horizontal(Button("Login", id="do_login", classes="-primary"), Button("Exit", id="exit")),
            Horizontal(Button("Settings", id="open_settings"), Button("Create User", id="open_create"), Button("Reset Password", id="open_reset")),
            id="modal-card",
            classes="layer-ui",
        )

        yield Footer()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "do_login":
            username = self.query_one("#username", Input).value.strip()
            password = self.query_one("#password", Input).value
            try:
                sess = await login_user(username, password)
                self.app.session = sess
                await self.app.push_screen(JournalHomeScreen())
            except Exception as exc:
                self.app.notify(str(exc))
        elif bid == "exit":
            self.app.exit()
        elif bid == "open_settings":
            await self.app.push_screen(SettingsModal())
        elif bid == "open_create":
            await self.app.push_screen(CreateUserModal())
        elif bid == "open_reset":
            await self.app.push_screen(ResetPasswordModal())


class JournalHomeScreen(Screen):
    """Authenticated home: Browse / New Entry / Search / Account tabs."""

    BINDINGS = [Binding("escape", "app.pop_screen", "Back")]
    LAYERS = ("bg", "ui")

    def compose(self) -> ComposeResult:
        cfg = load_config()
        if cfg.get("ascii_art_enabled", True) and cfg.get("ascii_art"):
            yield Static(str(cfg.get("ascii_art")), id="ascii", markup=False)

        yield Header()

        with Container(id="modal-card", classes="layer-ui"):
            #yield Static("Home", classes="title")
            with TabbedContent():
                with TabPane("Browse"):
                    self.list_view = ListView()
                    yield self.list_view
                with TabPane("New Entry"):
                    self.title_in = Input(placeholder="title")
                    self.body_in = TextArea(placeholder="body (Ctrl+Enter to save)",show_cursor=True)
                    yield self.title_in
                    yield self.body_in
                    yield Button("Save Entry", id="save_entry", classes="-primary")
                with TabPane("Search"):
                    self.query_in = Input(placeholder="search tokens (AND)")
                    yield self.query_in
                    yield Button("Search", id="do_search")
                    self.search_results = ListView()
                    yield self.search_results
                with TabPane("Account"):
                    self.user_label = Static("", classes="hint")
                    yield self.user_label
                    yield Horizontal(
                        Button("Settings", id="open_settings"),
                        Button("Logout", id="logout")
                    )

        yield Footer()

    async def on_mount(self) -> None:
        await self.refresh_list()

    async def refresh_list(self) -> None:
        self.list_view.clear()
        entries = await list_entries(self.app.session)
        for eid, created_at, title in entries:
            item = ListItem(Label(f"{created_at} — {title}"))
            item.data = eid
            self.list_view.append(item)

    async def on_list_view_selected(self, message: ListView.Selected) -> None:
        await self.app.push_screen(ViewEntryScreen(entry_id=message.item.data))

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "save_entry":
            t = self.title_in.value.strip()
            b = self.body_in.text
            if not t or not b.strip():
                self.app.notify("Title and body required")
                return
            await add_entry(self.app.session, t, b)
            self.title_in.value = ""
            self.body_in.text = ""
            await self.refresh_list()
            self.app.notify("Entry saved")
        elif bid == "do_search":
            q = self.query_in.value
            ids = await search_entries(self.app.session, q)
            self.search_results.clear()
            if not ids:
                self.search_results.append(ListItem(Label("No results.")))
                return
            for eid in ids:
                created_at, title, _ = await get_entry(self.app.session, eid)
                li = ListItem(Label(f"{created_at} — {title}"))
                li.data = eid
                self.search_results.append(li)
        elif bid == "open_settings":
            self.app.push_screen(SettingsModal())
        elif bid == "logout":
            self.app.session = None
            self.app.pop_screen()

class ViewEntryScreen(Screen):
    """View a single journal entry."""

    BINDINGS = [("escape", "app.pop_screen", "Back")]
    LAYERS = ("bg", "ui")

    def __init__(self, entry_id: int):
        super().__init__()
        self.entry_id = entry_id

    def compose(self) -> ComposeResult:
        cfg = load_config()

        with Container(id="base-frame", classes="layer-bg"):
            if cfg.get("ascii_art_enabled", True) and cfg.get("ascii_art"):
                yield Static(str(cfg.get("ascii_art")), id="ascii", markup=False)

        yield Header()

        with Container(id="modal-card", classes="layer-ui"):
            self.title_label = Static("", classes="title")
            yield self.title_label

            self.meta_label = Static("", classes="hint")
            yield self.meta_label

            self.body_label = Static("", id="entry-body", expand=True)
            yield self.body_label

            yield Horizontal(
                Button("Edit", id="edit", classes="-primary"),
                Button("Delete", id="delete"),
                Button("Back", id="back")
            )

        yield Footer()

    async def on_mount(self) -> None:
        # Load entry data
        created_at, title, body = await get_entry(self.app.session, self.entry_id)
        self.title_label.update(title)
        self.meta_label.update(f"Created: {created_at}")
        self.body_label.update(body)
        self.set_focus(self.title_label)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "back":
            self.app.pop_screen()
        elif bid == "edit":
            await self.app.push_screen(EditEntryModal(self.entry_id))
        elif bid == "delete":
            await delete_entry(self.app.session, self.entry_id)
            self.app.notify("Entry deleted.")
            self.app.pop_screen()  # close the view screen
            # Refresh home list if visible
            if hasattr(self.app.screen_stack[-1], "refresh_list"):
                await self.app.screen_stack[-1].refresh_list()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

class CyberJournalApp(App):
    """Textual App wrapper. Loads CSS, DB, and initial screen; applies theme."""

    TITLE = "CYBER//JOURNAL"
    CSS_PATH = THEME_CSS_PATH
    session: Optional[SessionKeys] = None
    async def on_mount(self) -> None:
        await init_db()
        # Apply saved theme class before first screen
        cfg = load_config()
        _apply_app_theme(self, str(cfg.get("active_theme", "vt220_green")))
        await self.push_screen(LoginScreen())


if __name__ == "__main__":
    import asyncio
    asyncio.run(CyberJournalApp().run_async())
