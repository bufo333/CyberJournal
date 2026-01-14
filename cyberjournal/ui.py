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
    get_security_question,
    change_password_logged_in,
    reset_password_with_security_answer,
    add_entry,
    list_entries,
    get_entry,
    search_entries,
    update_entry,
    delete_entry,
    get_entry_with_map,
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
            Static("Security question required for password reset.", classes="hint"),
            Input(placeholder="security question", id="sq"),
            Input(placeholder="security answer", password=True, id="sa"),
            Horizontal(Button("Create", id="create", classes="-primary"), Button("Close", id="close")),
            id="modal-card",
            classes="layer-ui",
        )

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "create":
            u = self.query_one("#u", Input).value.strip()
            p = self.query_one("#p", Input).value
            c = self.query_one("#c", Input).value
            sq = self.query_one("#sq", Input).value.strip()
            sa = self.query_one("#sa", Input).value
            if not u or not p or p != c:
                self.app.notify("Invalid username/password")
                return
            if not sq or not sa:
                self.app.notify("Security question and answer required")
                return
            try:
                await register_user(u, p, sq, sa)
                self.app.notify("User created.")
                self.app.pop_screen()
            except Exception as exc:
                self.app.notify(str(exc))
        elif event.button.id == "close":
            self.app.pop_screen()


class ResetPasswordModal(ModalScreen[None]):
    """Password reset flow that wipes encrypted entries."""

    def __init__(self) -> None:
        super().__init__()
        self.target_username: Optional[str] = None

    def compose(self) -> ComposeResult:
        yield Container(
            Static("RESET PASSWORD", classes="title"),
            Static("Resetting will permanently delete all entries.", classes="hint"),
            Input(placeholder="username", id="u"),
            Button("Load Question", id="load_question"),
            Static("", id="question", classes="hint"),
            Input(placeholder="security answer", password=True, id="answer", disabled=True),
            Input(placeholder="new password", password=True, id="p1", disabled=True),
            Input(placeholder="confirm new", password=True, id="p2", disabled=True),
            Horizontal(Button("Reset", id="reset", classes="-primary"), Button("Close", id="close")),
            id="modal-card",
            classes="layer-ui",
        )

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "load_question":
            username = self.query_one("#u", Input).value.strip()
            if not username:
                self.app.notify("Username required")
                return
            try:
                question = await get_security_question(username)
            except Exception as exc:
                self.app.notify(str(exc))
                return

            self.target_username = username
            self.query_one("#question", Static).update(f"Security question: {question}")
            self.query_one("#answer", Input).disabled = False
            self.query_one("#p1", Input).disabled = False
            self.query_one("#p2", Input).disabled = False
        elif bid == "reset":
            if not self.target_username:
                self.app.notify("Load security question first")
                return
            answer = self.query_one("#answer", Input).value
            p1 = self.query_one("#p1", Input).value
            p2 = self.query_one("#p2", Input).value
            if not answer or not p1 or p1 != p2:
                self.app.notify("Invalid security answer or password")
                return
            try:
                await reset_password_with_security_answer(self.target_username, answer, p1)
                self.app.notify("Password reset complete. Entries deleted.")
                self.app.pop_screen()
            except Exception as exc:
                self.app.notify(str(exc))
        elif bid == "close":
            self.app.pop_screen()

class ChangePasswordModal(ModalScreen[None]):
    """Change the active account password and re-encrypt data."""

    def compose(self) -> ComposeResult:
        yield Container(
            Static("CHANGE PASSWORD", classes="title"),
            Input(placeholder="current password", password=True, id="p0"),
            Input(placeholder="new password", password=True, id="p1"),
            Input(placeholder="confirm new", password=True, id="p2"),
            Horizontal(Button("Save", id="save", classes="-primary"), Button("Close", id="close")),
            id="modal-card",
            classes="layer-ui",
        )

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "save":
            if not self.app.session:
                self.app.notify("Not logged in")
                return
            p0 = self.query_one("#p0", Input).value
            p1 = self.query_one("#p1", Input).value
            p2 = self.query_one("#p2", Input).value
            if not p0 or not p1 or p1 != p2:
                self.app.notify("Invalid password")
                return
            try:
                self.app.session = await change_password_logged_in(self.app.session, p0, p1)
                self.app.notify("Password updated.")
                self.app.pop_screen()
            except Exception as exc:
                self.app.notify(str(exc))
        elif bid == "close":
            self.app.pop_screen()


class EditEntryModal(ModalScreen[None]):
    """Edit title/body of an entry. Map is regenerated by logic on save (phase 2/3 design)."""
    AUTO_DISMISS = False

    def __init__(self, entry_id: int) -> None:
        super().__init__()
        self.entry_id = entry_id

    async def on_mount(self) -> None:
        # prime inputs with current entry
        created_at, title, body = await get_entry(self.app.session, self.entry_id)
        self.query_one("#etitle", Input).value = title
        self.query_one("#ebody", TextArea).text = body

    def compose(self) -> ComposeResult:
        yield Container(
            Static("EDIT ENTRY", classes="title"),
            Input(placeholder="title", id="etitle"),
            TextArea(placeholder="body", id="ebody"),
            Horizontal(
                Button("Save", id="save", classes="-primary"),
                Button("Cancel", id="cancel")
            ),
            id="modal-card", classes="layer-ui",
        )

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "save":
            title = self.query_one("#etitle", Input).value.strip()
            body  = self.query_one("#ebody", TextArea).text
            if not title or not body.strip():
                self.app.notify("Title and body required")
                return
            try:
                # logic.update_entry must re-encrypt title/body, rebuild terms,
                # and (phase 3) regenerate & store an updated map.
                await update_entry(self.app.session, self.entry_id, title, body)
                self.app.notify("Entry updated")
                # Close editor, also refresh underlying view screen if mounted
                self.app.pop_screen()
                # If we came from ViewEntryScreen, reload it:
                await self.app.pop_screen()
                await self.app.push_screen(ViewEntryScreen(self.entry_id))
            except Exception as exc:
                self.app.notify(str(exc))
        elif bid == "cancel":
            self.app.pop_screen()


class ConfirmDeleteModal(ModalScreen[None]):
    """Confirm deleting an entry."""
    AUTO_DISMISS = False

    def __init__(self, entry_id: int) -> None:
        super().__init__()
        self.entry_id = entry_id

    def compose(self) -> ComposeResult:
        yield Container(
            Static("DELETE ENTRY?", classes="title"),
            Static("This cannot be undone."),
            Horizontal(
                Button("Delete", id="yes", classes="-primary"),
                Button("Cancel", id="no")
            ),
            id="modal-card", classes="layer-ui",
        )

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if (event.button.id or "") == "yes":
            try:
                await delete_entry(self.app.session, self.entry_id)
                self.app.notify("Entry deleted")
                self.app.pop_screen()      # close confirm
                await self.app.pop_screen()  # close view screen
            except Exception as exc:
                self.app.notify(str(exc))
        else:
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
            yield Static(str(cfg.get("ascii_art")), id="ascii", classes="layer-bg", markup=False)
        yield Header(classes="layer-ui")
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

        yield Footer(classes="layer-ui")

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
            yield Static(str(cfg.get("ascii_art")), id="ascii", classes="layer-bg", markup=False)

        yield Header(classes="layer-ui")

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
                        Button("Change Password", id="change_password"),
                        Button("Logout", id="logout")
                    )

        yield Footer(classes="layer-ui")

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
                try:
                    created_at, title, _ = await get_entry(self.app.session, eid)
                except ValueError:
                    # Entry was deleted or otherwise missing; just skip it quietly
                    continue

                li = ListItem(Label(f"{created_at} — {title}"))
                li.data = eid
                self.search_results.append(li)

        elif bid == "open_settings":
            self.app.push_screen(SettingsModal())
        elif bid == "change_password":
            self.app.push_screen(ChangePasswordModal())
        elif bid == "logout":
            self.app.session = None
            self.app.pop_screen()

class ViewEntryScreen(Screen):
    """View a single journal entry with map side-by-side."""

    BINDINGS = [Binding("escape", "app.pop_screen", "Back")]
    LAYERS = ("bg", "ui")

    def __init__(self, entry_id: int) -> None:
        super().__init__()
        self.entry_id = entry_id

    def compose(self) -> ComposeResult:
        cfg = load_config()

        # Background (ASCII, optional)
        if cfg.get("ascii_art_enabled", True) and cfg.get("ascii_art"):
            yield Static(str(cfg.get("ascii_art")), id="ascii", classes="layer-bg", markup=False)

        # Header
        yield Header(classes="layer-ui")

        # Modal card with a single row that contains: [ map | body ]
        with Container(id="modal-card", classes="layer-ui"):
            self.title_label = Static("", classes="title")
            yield self.title_label

            self.meta_label = Static("", classes="hint")
            yield self.meta_label

            # Two columns in a Horizontal container
            with Horizontal(id="entry-row"):
                # Left: map
                with Container(id="map-box"):
                    self.map_label = Static("", id="map", markup=False)
                    yield self.map_label

                # Right: body (read only, scrollable)
                with Container(id="body-box"):
                    self.body_area = TextArea(id="entry-text", read_only=True)
                    yield self.body_area

            # Actions
            with Horizontal(id="actions"):
                yield Button("Edit", id="edit", classes="-primary")
                yield Button("Delete", id="delete")
                yield Button("Back", id="back")

        # Footer
        yield Footer(classes="layer-ui")

    async def on_mount(self) -> None:
        """Load entry + (optionally) map."""
        result = await get_entry_with_map(self.app.session, self.entry_id)
        # Support both (created_at, title, body) and (… , map_text)
        map_text = None
        try:
            created_at, title, body, map_text, map_format = result  # type: ignore[misc]
        except Exception:
            created_at, title, body = result  # type: ignore[misc]

        self.title_label.update(title)
        self.meta_label.update(f"Created: {created_at}")
        self.body_area.text = body or ""

        if map_text:
            # Show preformatted ASCII without wrapping
            self.map_label.update(map_text)
        else:
            self.map_label.update("(no map)")

        # Put the caret in the body pane for immediate scrolling with arrows
        self.set_focus(self.body_area)

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
