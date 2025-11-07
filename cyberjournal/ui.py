# =====================
# File: cyberjournal/ui.py
# =====================
"""Textual TUI for Cyberjournal.

UI-only code; all business logic is in :mod:`cyberjournal.logic`.
"""
from __future__ import annotations

from typing import Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.widgets import Header, Footer, Button, Static, Input, ListView, ListItem, TextArea, TabbedContent, TabPane, Label
from textual.screen import Screen

from .logic import (
    initialize,
    register_user,
    login_user,
    add_entry,
    list_entries,
    get_entry,
    search_entries,
    SessionKeys,
)


class MainMenuScreen(Screen):
    """Landing screen: login / register / quit."""

    def compose(self) -> ComposeResult:  # noqa: D401 - UI composition
        yield Header(show_clock=True)
        with Container(id="card"):
            yield Static("CYBER//JOURNAL", classes="title")
            yield Button("Login", id="login", classes="-primary")
            yield Button("Register New User", id="register")
            yield Button("Quit", id="quit")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        match event.button.id:
            case "login":
                self.app.push_screen(LoginScreen())
            case "register":
                self.app.push_screen(RegisterScreen())
            case "quit":
                self.app.exit()


class LoginScreen(Screen):
    """Username/password login flow."""

    BINDINGS = [Binding("escape", "app.pop_screen", "Back")]

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="card"):
            yield Static("LOGIN", classes="title")
            self.username = Input(placeholder="username")
            self.password = Input(placeholder="password", password=True)
            yield self.username
            yield self.password
            yield Button("Login", id="do_login", classes="-primary")
            self.status = Static("", classes="error")
            yield self.status
        yield Footer()

    async def on_button_pressed(self, event: Button.Pressed) -> None:  # noqa: D401
        if event.button.id == "do_login":
            try:
                sess = await login_user(self.username.value.strip(), self.password.value)
                self.app.session = sess
                await self.app.push_screen(JournalHomeScreen())
            except Exception as exc:  # pragma: no cover - interactive path
                self.status.update(f"\n[!] {exc}")


class RegisterScreen(Screen):
    """Create a new user from the main menu."""

    BINDINGS = [Binding("escape", "app.pop_screen", "Back")]

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="card"):
            yield Static("REGISTER USER", classes="title")
            self.username = Input(placeholder="new username")
            self.password = Input(placeholder="password", password=True)
            self.confirm = Input(placeholder="confirm password", password=True)
            yield self.username
            yield self.password
            yield self.confirm
            yield Button("Create", id="do_register", classes="-primary")
            self.status = Static("", classes="error")
            yield self.status
        yield Footer()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "do_register":
            u = self.username.value.strip()
            p = self.password.value
            c = self.confirm.value
            if not u or not p:
                self.status.update("Username/password required")
                return
            if p != c:
                self.status.update("Passwords do not match")
                return
            try:
                await register_user(u, p)
                self.status.update("User created. Press Esc to go back and login.")
            except Exception as exc:  # pragma: no cover - interactive path
                self.status.update(f"[!] {exc}")


class JournalHomeScreen(Screen):
    """Main journal UI with tabs for browsing, adding, and searching entries."""

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="card"):
            yield Static("JOURNAL", classes="title")
            with TabbedContent():
                with TabPane("Browse"):
                    self.list_view = ListView()
                    yield self.list_view
                with TabPane("New Entry"):
                    self.title_in = Input(placeholder="title")
                    self.body_in = TextArea(placeholder="body (Ctrl+Enter to save)")
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
                    yield Static(lambda: f"Logged in as: {self.app.session.username}")
                    yield Button("Logout", id="logout")
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
        eid = message.item.data
        await self.app.push_screen(ViewEntryScreen(entry_id=eid))

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        match event.button.id:
            case "save_entry":
                t = self.title_in.value.strip()
                b = self.body_in.text
                if not t or not b.strip():
                    await self.app.push_screen(InfoScreen("Title and body required."))
                    return
                await add_entry(self.app.session, t, b)
                self.title_in.value = ""
                self.body_in.text = ""
                await self.refresh_list()
                await self.app.push_screen(InfoScreen("Entry saved."))
            case "do_search":
                q = self.query_in.value
                ids = await search_entries(self.app.session, q)
                self.search_results.clear()
                if not ids:
                    self.search_results.append(ListItem(Label("No results.")))
                    return
                for eid in ids:
                    created_at, title, _ = await get_entry(self.app.session, eid)
                    item = ListItem(Label(f"{created_at} — {title}"))
                    item.data = eid
                    self.search_results.append(item)
            case "logout":
                self.app.session = None
                await self.app.pop_screen()

    async def on_list_view_submitted(self, message: ListView.Submitted) -> None:
        if message.list_view is self.search_results:
            eid = message.item.data
            await self.app.push_screen(ViewEntryScreen(entry_id=eid))


class ViewEntryScreen(Screen):
    """Read-only view of a single entry."""

    def __init__(self, entry_id: int) -> None:
        super().__init__()
        self.entry_id = entry_id

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="card"):
            self.title = Static("", classes="title")
            self.meta = Static("", classes="hint")
            self.body = Static("")
            yield self.title
            yield self.meta
            yield self.body
            yield Button("Back", id="back")
        yield Footer()

    async def on_mount(self) -> None:
        created_at, title, body = await get_entry(self.app.session, self.entry_id)
        self.title.update(title)
        self.meta.update(f"Created: {created_at}")
        self.body.update(body)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back":
            self.app.pop_screen()


class InfoScreen(Screen):
    """Modal with a single message and OK button."""

    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Container(id="card"):
            yield Static(self.message)
            yield Button("OK", id="ok")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ok":
            self.app.pop_screen()


class CyberJournalApp(App):
    """Textual application wiring screens and session."""

    CSS_PATH = "theme.css"
    TITLE = "CYBER//JOURNAL"
    session: Optional[SessionKeys] = None

    async def on_mount(self) -> None:  # noqa: D401 - app lifecycle
        await initialize()
        await self.push_screen(MainMenuScreen())

