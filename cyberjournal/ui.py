# -*- coding: utf-8 -*-
"""Textual UI for CyberJournal.

Theme switching:
    We use a single theme.css with 3 variants (vt220/amber/neon) implemented
    as CSS class scopes: `.theme-vt220`, `.theme-amber`, `.theme-neon`.
    The app toggles one of these classes at runtime based on the saved config.
"""
from __future__ import annotations

import calendar as cal_mod
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Grid, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.timer import Timer
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    Select,
    Static,
    TabPane,
    TabbedContent,
    TextArea,
)

logger = logging.getLogger(__name__)

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
    get_entry_full,
    toggle_favorite,
    list_entries_paginated,
    count_entries,
    add_tag,
    remove_tag,
    list_tags,
    search_by_tag,
    set_mood_weather,
    MOOD_CHOICES,
    create_notebook,
    list_notebooks,
    delete_notebook,
    assign_entry_notebook,
    create_template,
    list_templates,
    get_template,
    delete_template,
    get_calendar_data,
    export_entries,
    import_entries,
    save_draft,
    get_draft,
    list_entries_in_range,
)
from cyberjournal.world.explorer import WorldExplorerScreen

THEME_CSS_PATH = str(Path(__file__).with_name("theme.css"))

PAGE_SIZE = 20


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _apply_app_theme(app: App, theme_key: str) -> None:
    valid = {
        "vt220_green": "theme-vt220",
        "as400_amber": "theme-amber",
        "vector_neon": "theme-neon",
    }
    target = valid.get(theme_key, "theme-vt220")
    for cls in ("theme-vt220", "theme-amber", "theme-neon"):
        app.set_class(False, cls)
    app.set_class(True, target)


# ---------------------------------------------------------------------------
# Modals
# ---------------------------------------------------------------------------

class SettingsModal(ModalScreen[None]):
    """Settings: theme choice + ASCII background text."""

    def compose(self) -> ComposeResult:
        cfg = load_config()
        active = str(cfg.get("active_theme", "vt220_green"))
        ascii_enabled = bool(cfg.get("ascii_art_enabled", True))

        yield Container(
            Static("SETTINGS", classes="title"),
            Static("Theme", classes="hint"),
            Horizontal(
                Button("VT220 GREEN", id="t_green", classes="-primary" if active == "vt220_green" else ""),
                Button("AS/400 AMBER", id="t_amber", classes="-primary" if active == "as400_amber" else ""),
                Button("VECTOR NEON", id="t_neon", classes="-primary" if active == "vector_neon" else ""),
            ),
            Static("", classes="separator"),
            Static("ASCII art background (on/off)", classes="hint"),
            Input(value="on" if ascii_enabled else "off", id="ascii_toggle"),
            TextArea(id="ascii_text", placeholder="Paste ASCII art here..."),
            Horizontal(Button("Save", id="save", classes="-primary"), Button("Close", id="close")),
            id="narrow-card",
            classes="layer-ui",
        )

    async def on_button_pressed(self, event: Button.Pressed) -> None:
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
            _apply_app_theme(self.app, str(cfg.get("active_theme", "vt220_green")))
            self.app.notify("Settings saved.")
            self.dismiss()
            return
        elif bid == "close":
            self.dismiss()
            return
        save_config(cfg)
        _apply_app_theme(self.app, str(cfg.get("active_theme", "vt220_green")))
        self.dismiss()
        await self.app.push_screen(SettingsModal())


class CreateUserModal(ModalScreen[None]):
    """New user registration overlay."""

    def compose(self) -> ComposeResult:
        yield Container(
            Static("CREATE USER", classes="title"),
            Input(placeholder="username", id="u"),
            Input(placeholder="password", password=True, id="p"),
            Input(placeholder="confirm password", password=True, id="c"),
            Static("", classes="separator"),
            Static("Security question (for password reset)", classes="hint"),
            Input(placeholder="security question", id="sq"),
            Input(placeholder="security answer", password=True, id="sa"),
            Horizontal(Button("Create", id="create", classes="-primary"), Button("Close", id="close")),
            id="narrow-card",
            classes="layer-ui",
        )

    def on_mount(self) -> None:
        self.set_focus(self.query_one("#u", Input))

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
                self.dismiss()
            except Exception as exc:
                logger.exception("Registration failed")
                self.app.notify(str(exc))
        elif event.button.id == "close":
            self.dismiss()


class ResetPasswordModal(ModalScreen[None]):
    """Password reset flow that wipes encrypted entries."""

    def __init__(self) -> None:
        super().__init__()
        self.target_username: Optional[str] = None

    def compose(self) -> ComposeResult:
        yield Container(
            Static("RESET PASSWORD", classes="title"),
            Static("Warning: resetting will permanently delete all entries.", classes="error"),
            Input(placeholder="username", id="u"),
            Button("Load Question", id="load_question"),
            Static("", id="question", classes="hint"),
            Static("", classes="separator"),
            Input(placeholder="security answer", password=True, id="answer", disabled=True),
            Input(placeholder="new password", password=True, id="p1", disabled=True),
            Input(placeholder="confirm new password", password=True, id="p2", disabled=True),
            Horizontal(Button("Reset", id="reset", classes="-primary"), Button("Close", id="close")),
            id="narrow-card",
            classes="layer-ui",
        )

    def on_mount(self) -> None:
        self.set_focus(self.query_one("#u", Input))

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
                self.dismiss()
            except Exception as exc:
                logger.exception("Password reset failed")
                self.app.notify(str(exc))
        elif bid == "close":
            self.dismiss()


class ChangePasswordModal(ModalScreen[None]):
    """Change the active account password and re-encrypt data."""

    def compose(self) -> ComposeResult:
        yield Container(
            Static("CHANGE PASSWORD", classes="title"),
            Input(placeholder="current password", password=True, id="p0"),
            Static("", classes="separator"),
            Input(placeholder="new password", password=True, id="p1"),
            Input(placeholder="confirm new password", password=True, id="p2"),
            Horizontal(Button("Save", id="save", classes="-primary"), Button("Close", id="close")),
            id="narrow-card",
            classes="layer-ui",
        )

    def on_mount(self) -> None:
        self.set_focus(self.query_one("#p0", Input))

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
                self.dismiss()
            except Exception as exc:
                logger.exception("Password change failed")
                self.app.notify(str(exc))
        elif bid == "close":
            self.dismiss()


class EditEntryModal(ModalScreen[None]):
    """Edit title/body of an entry."""
    AUTO_DISMISS = False

    def __init__(self, entry_id: int) -> None:
        super().__init__()
        self.entry_id = entry_id

    async def on_mount(self) -> None:
        try:
            created_at, title, body = await get_entry(self.app.session, self.entry_id)
            self.query_one("#etitle", Input).value = title
            self.query_one("#ebody", TextArea).text = body
            self.set_focus(self.query_one("#etitle", Input))
        except Exception as exc:
            logger.exception("Failed to load entry for editing")
            self.app.notify(f"Could not load entry: {exc}")
            self.dismiss()

    def compose(self) -> ComposeResult:
        yield Container(
            Static("EDIT ENTRY", classes="title"),
            Input(placeholder="title", id="etitle"),
            TextArea(placeholder="body", id="ebody"),
            Horizontal(
                Button("Save", id="save", classes="-primary"),
                Button("Cancel", id="cancel")
            ),
            id="narrow-card", classes="layer-ui",
        )

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "save":
            title = self.query_one("#etitle", Input).value.strip()
            body = self.query_one("#ebody", TextArea).text
            if not title or not body.strip():
                self.app.notify("Title and body required")
                return
            try:
                await update_entry(self.app.session, self.entry_id, title, body)
                self.app.notify("Entry updated")
                self.dismiss()
            except Exception as exc:
                logger.exception("Entry update failed")
                self.app.notify(str(exc))
        elif bid == "cancel":
            self.dismiss()


class ConfirmDeleteModal(ModalScreen):
    """Confirm deleting an entry."""
    AUTO_DISMISS = False

    def __init__(self, entry_id: int) -> None:
        super().__init__()
        self.entry_id = entry_id

    def compose(self) -> ComposeResult:
        yield Container(
            Static("DELETE ENTRY?", classes="title"),
            Static("This action cannot be undone.", classes="error"),
            Horizontal(
                Button("Delete", id="yes", classes="-primary"),
                Button("Cancel", id="no")
            ),
            id="narrow-card", classes="layer-ui",
        )

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if (event.button.id or "") == "yes":
            try:
                await delete_entry(self.app.session, self.entry_id)
                self.app.notify("Entry deleted")
                self.dismiss("deleted")
            except Exception as exc:
                logger.exception("Entry deletion failed")
                self.app.notify(str(exc))
        else:
            self.dismiss()


class TagModal(ModalScreen[None]):
    """Manage tags on an entry."""

    def __init__(self, entry_id: int) -> None:
        super().__init__()
        self.entry_id = entry_id

    def compose(self) -> ComposeResult:
        yield Container(
            Static("TAGS", classes="title"),
            Static("Select a tag to remove it.", classes="hint"),
            self._tag_list_widget(),
            Horizontal(
                Input(placeholder="add tag", id="tag_input"),
                Button("Add", id="add_tag", classes="-primary"),
            ),
            Button("Close", id="close"),
            id="narrow-card", classes="layer-ui",
        )

    def _tag_list_widget(self) -> ListView:
        self.tag_list = ListView(id="tag-list")
        return self.tag_list

    async def on_mount(self) -> None:
        await self._refresh_tags()
        self.set_focus(self.query_one("#tag_input", Input))

    async def _refresh_tags(self) -> None:
        self.tag_list.clear()
        tags = await list_tags(self.app.session, self.entry_id)
        if not tags:
            self.tag_list.append(ListItem(Label("No tags")))
            return
        for tag_id, tag_text in tags:
            li = ListItem(Label(f"  {tag_text}  [x]"))
            li.data = tag_id
            self.tag_list.append(li)

    async def _add_tag(self) -> None:
        tag_input = self.query_one("#tag_input", Input)
        tag = tag_input.value.strip()
        if tag:
            try:
                await add_tag(self.app.session, self.entry_id, tag)
                tag_input.value = ""
                await self._refresh_tags()
            except Exception as exc:
                self.app.notify(str(exc))

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "tag_input":
            await self._add_tag()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "add_tag":
            await self._add_tag()
        elif bid == "close":
            self.dismiss()

    async def on_list_view_selected(self, message: ListView.Selected) -> None:
        tag_id = getattr(message.item, "data", None)
        if tag_id is not None:
            await remove_tag(self.app.session, tag_id)
            await self._refresh_tags()


class NotebookModal(ModalScreen[None]):
    """Manage notebooks — create, delete, and select for filtering."""

    def __init__(self, current_filter: int | None = None) -> None:
        super().__init__()
        self._current_filter = current_filter

    def compose(self) -> ComposeResult:
        yield Container(
            Static("NOTEBOOKS", classes="title"),
            Static("Select to filter  |  Select active to delete", classes="hint"),
            self._nb_list_widget(),
            Static("", classes="separator"),
            Horizontal(
                Input(placeholder="new notebook name", id="nb_input"),
                Button("Create", id="create_nb", classes="-primary"),
            ),
            Horizontal(
                Button("Show All", id="show_all"),
                Button("Close", id="close"),
            ),
            id="narrow-card", classes="layer-ui",
        )

    def _nb_list_widget(self) -> ListView:
        self.nb_list = ListView(id="nb-list")
        return self.nb_list

    async def on_mount(self) -> None:
        await self._refresh()
        self.set_focus(self.query_one("#nb_input", Input))

    async def _refresh(self) -> None:
        self.nb_list.clear()
        notebooks = await list_notebooks(self.app.session)
        if not notebooks:
            self.nb_list.append(ListItem(Label("No notebooks yet — create one below.")))
            return
        for nb_id, name in notebooks:
            active = " (active)" if nb_id == self._current_filter else ""
            li = ListItem(Label(f"  {name}{active}"))
            li.data = {"id": nb_id, "name": name}
            self.nb_list.append(li)

    async def _create_notebook(self) -> None:
        nb_input = self.query_one("#nb_input", Input)
        name = nb_input.value.strip()
        if name:
            try:
                await create_notebook(self.app.session, name)
                nb_input.value = ""
                self.app.notify(f"Notebook '{name}' created")
                await self._refresh()
            except Exception as exc:
                self.app.notify(str(exc))

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "nb_input":
            await self._create_notebook()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "create_nb":
            await self._create_notebook()
        elif bid == "show_all":
            self.dismiss(None)  # signal: clear notebook filter
        elif bid == "close":
            self.dismiss(-1)  # signal: no change

    async def on_list_view_selected(self, message: ListView.Selected) -> None:
        data = getattr(message.item, "data", None)
        if data is None or not isinstance(data, dict):
            return
        nb_id = data["id"]
        nb_name = data["name"]
        # If already the active filter, treat as delete
        if nb_id == self._current_filter:
            await delete_notebook(self.app.session, nb_id)
            self.app.notify(f"Notebook '{nb_name}' deleted")
            self.dismiss(None)  # clear filter since active notebook deleted
        else:
            self.dismiss(nb_id)  # signal: filter by this notebook


class TemplateModal(ModalScreen[None]):
    """Manage and apply entry templates."""

    def __init__(self, on_select=None) -> None:
        super().__init__()
        self._on_select = on_select

    def compose(self) -> ComposeResult:
        yield Container(
            Static("TEMPLATES", classes="title"),
            Static("Select a template to apply it.", classes="hint"),
            self._tpl_list_widget(),
            Static("", classes="separator"),
            Horizontal(
                Input(placeholder="template name", id="tpl_name"),
                Button("Save", id="save_tpl", classes="-primary"),
            ),
            Button("Close", id="close"),
            id="narrow-card", classes="layer-ui",
        )

    def _tpl_list_widget(self) -> ListView:
        self.tpl_list = ListView(id="tpl-list")
        return self.tpl_list

    async def on_mount(self) -> None:
        await self._refresh()

    async def _refresh(self) -> None:
        self.tpl_list.clear()
        templates = await list_templates(self.app.session)
        if not templates:
            self.tpl_list.append(ListItem(Label("No templates")))
            return
        for tpl_id, name in templates:
            li = ListItem(Label(f"  {name}"))
            li.data = tpl_id
            self.tpl_list.append(li)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "save_tpl":
            name = self.query_one("#tpl_name", Input).value.strip()
            if not name:
                self.app.notify("Template name required")
                return
            try:
                await create_template(self.app.session, name, "", "")
                self.app.notify("Template saved")
                await self._refresh()
            except Exception as exc:
                self.app.notify(str(exc))
        elif bid == "close":
            self.dismiss()

    async def on_list_view_selected(self, message: ListView.Selected) -> None:
        tpl_id = getattr(message.item, "data", None)
        if tpl_id is not None and self._on_select:
            try:
                name, title, body = await get_template(self.app.session, tpl_id)
                self._on_select(title, body)
                self.dismiss()
            except Exception as exc:
                self.app.notify(str(exc))


class ExportModal(ModalScreen[None]):
    """Export entries to file."""

    def compose(self) -> ComposeResult:
        yield Container(
            Static("EXPORT ENTRIES", classes="title"),
            Static("Choose format, then copy from the output below.", classes="hint"),
            Horizontal(
                Button("JSON", id="exp_json", classes="-primary"),
                Button("Markdown", id="exp_md"),
            ),
            TextArea(id="export_output", read_only=True),
            Button("Close", id="close"),
            id="narrow-card", classes="layer-ui",
        )

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "exp_json":
            data = await export_entries(self.app.session, "json")
            self.query_one("#export_output", TextArea).text = data
            self.app.notify("Exported as JSON — copy from the text area")
        elif bid == "exp_md":
            data = await export_entries(self.app.session, "markdown")
            self.query_one("#export_output", TextArea).text = data
            self.app.notify("Exported as Markdown — copy from the text area")
        elif bid == "close":
            self.dismiss()


class ImportModal(ModalScreen[None]):
    """Import entries from JSON."""

    def compose(self) -> ComposeResult:
        yield Container(
            Static("IMPORT ENTRIES", classes="title"),
            Static("Paste a JSON array of entries:", classes="hint"),
            TextArea(id="import_input", placeholder='[{"title": "...", "body": "..."}]'),
            Horizontal(
                Button("Import", id="do_import", classes="-primary"),
                Button("Close", id="close"),
            ),
            id="narrow-card", classes="layer-ui",
        )

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "do_import":
            data = self.query_one("#import_input", TextArea).text
            if not data.strip():
                self.app.notify("Paste JSON data first")
                return
            try:
                count = await import_entries(self.app.session, data)
                self.app.notify(f"Imported {count} entries")
                self.dismiss()
            except Exception as exc:
                logger.exception("Import failed")
                self.app.notify(f"Import failed: {exc}")
        elif bid == "close":
            self.dismiss()


class HelpModal(ModalScreen[None]):
    """Show keyboard shortcuts and help."""

    def compose(self) -> ComposeResult:
        yield Container(
            Static("KEYBOARD SHORTCUTS", classes="title"),
            Static(
                "ESC         Back / Quit from login\n"
                "Tab         Switch between tabs\n"
                "Enter       Select item in list\n"
                "Arrows      Navigate lists and scroll\n"
                "?           Show this help\n"
                "\n"
                "BROWSE\n"
                "  Select an entry to view it\n"
                "  Favorites sort first\n"
                "\n"
                "ENTRY VIEW\n"
                "  Edit / Delete / Fav / Tags buttons\n"
                "\n"
                "SEARCH\n"
                "  Space-separated tokens (AND)\n"
                "  tag:name to search by tag\n"
                "\n"
                "WORLD EXPLORER\n"
                "  Arrows    Move cursor\n"
                "  Enter     Inspect tile\n"
                "  Q         View quests\n"
                "  H         World history\n",
                markup=False,
            ),
            Button("Close", id="close"),
            id="narrow-card", classes="layer-ui",
        )

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close":
            self.dismiss()


# ---------------------------------------------------------------------------
# Screens
# ---------------------------------------------------------------------------

class LoginScreen(Screen):
    """Login screen."""

    BINDINGS = [Binding("escape", "app.quit", "Quit")]
    LAYERS = ("bg", "ui")

    def on_mount(self) -> None:
        self.set_focus(self.query_one("#username", Input))

    def on_screen_resume(self) -> None:
        self.call_after_refresh(self.set_focus, self.query_one("#username", Input))

    def compose(self) -> ComposeResult:
        cfg = load_config()
        if cfg.get("ascii_art_enabled", True) and cfg.get("ascii_art"):
            yield Static(str(cfg.get("ascii_art")), id="ascii", classes="layer-bg", markup=False)
        yield Header(classes="layer-ui")
        yield Container(
            Static("CYBER//JOURNAL", classes="title"),
            Input(placeholder="username", id="username"),
            Input(placeholder="password", password=True, id="password"),
            Horizontal(
                Button("Login", id="do_login", classes="-primary"),
                Button("Exit", id="exit"),
            ),
            Static("", classes="separator"),
            Horizontal(
                Button("Settings", id="open_settings"),
                Button("New User", id="open_create"),
                Button("Reset", id="open_reset"),
            ),
            id="login-card",
            classes="layer-ui",
        )
        yield Footer(classes="layer-ui")

    async def _do_login(self) -> None:
        username = self.query_one("#username", Input).value.strip()
        password = self.query_one("#password", Input).value
        try:
            sess = await login_user(username, password)
            self.app.session = sess
            await self.app.push_screen(JournalHomeScreen())
        except Exception as exc:
            self.app.notify(str(exc))

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id in ("username", "password"):
            await self._do_login()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "do_login":
            await self._do_login()
        elif bid == "exit":
            self.app.exit()
        elif bid == "open_settings":
            await self.app.push_screen(SettingsModal())
        elif bid == "open_create":
            await self.app.push_screen(CreateUserModal())
        elif bid == "open_reset":
            await self.app.push_screen(ResetPasswordModal())


class JournalHomeScreen(Screen):
    """Authenticated home: Browse / New Entry / Search / Calendar / Account tabs."""

    BINDINGS = [
        Binding("escape", "logout", "Logout"),
        Binding("question_mark", "show_help", "Help"),
    ]
    LAYERS = ("bg", "ui")

    def __init__(self) -> None:
        super().__init__()
        self._page = 0
        self._total = 0
        self._sort_asc = False
        self._notebook_filter: int | None = None
        self._auto_save_timer: Timer | None = None

    def compose(self) -> ComposeResult:
        cfg = load_config()
        if cfg.get("ascii_art_enabled", True) and cfg.get("ascii_art"):
            yield Static(str(cfg.get("ascii_art")), id="ascii", classes="layer-bg", markup=False)

        yield Header(classes="layer-ui")

        with Container(id="modal-card", classes="layer-ui"):
            with TabbedContent():
                with TabPane("Browse"):
                    with Horizontal(id="browse-controls"):
                        yield Button("Sort", id="toggle_sort", classes="-small")
                        yield Button("Notebooks", id="manage_notebooks", classes="-small")
                        yield Static("", id="page_info", classes="meta")
                    self.list_view = ListView()
                    yield self.list_view
                    with Horizontal(id="page-controls"):
                        yield Button("< Prev", id="page_prev", classes="-small")
                        yield Button("Next >", id="page_next", classes="-small")

                with TabPane("New Entry"):
                    self.title_in = Input(placeholder="Title")
                    yield self.title_in
                    self.body_in = TextArea(id="new-body", show_cursor=True)
                    yield self.body_in
                    with Horizontal():
                        yield Select(
                            [(m if m else "Mood", m) for m in [""] + MOOD_CHOICES],
                            id="mood_select",
                            value="",
                        )
                        self.nb_select = Select(
                            [("Notebook", "")],
                            id="nb_select",
                            value="",
                        )
                        yield self.nb_select
                    self.weather_in = Input(placeholder="Weather (optional)")
                    yield self.weather_in
                    with Horizontal():
                        yield Button("Save Entry", id="save_entry", classes="-primary")
                        yield Button("Templates", id="open_templates")

                with TabPane("Search"):
                    with Horizontal():
                        self.query_in = Input(placeholder="keywords (AND) or tag:name")
                        yield self.query_in
                        yield Button("Go", id="do_search", classes="-primary -small")
                    self.search_results = ListView()
                    yield self.search_results

                with TabPane("Calendar"):
                    with Horizontal(id="cal-nav"):
                        yield Button("<", id="cal_prev", classes="-small")
                        self.cal_label = Static("", classes="title")
                        yield self.cal_label
                        yield Button(">", id="cal_next", classes="-small")
                    self.cal_grid = Static("", id="cal-grid", markup=False)
                    yield self.cal_grid

                with TabPane("World"):
                    yield Static(
                        "Your journal entries build a living world.\n"
                        "Each entry generates terrain, biomes, settlements, and NPCs.\n"
                        "Explore to discover hidden landmarks and complete quests.",
                        classes="hint",
                    )
                    yield Button("Enter World", id="enter_world", classes="-primary")

                with TabPane("Account"):
                    self.user_label = Static("", classes="meta")
                    yield self.user_label
                    Static("", classes="separator")
                    with Horizontal():
                        yield Button("Settings", id="open_settings")
                        yield Button("Password", id="change_password")
                    with Horizontal():
                        yield Button("Export", id="export")
                        yield Button("Import", id="import")
                    Static("", classes="separator")
                    yield Button("Logout", id="logout")

        yield Footer(classes="layer-ui")

    async def on_mount(self) -> None:
        if self.app.session:
            self.user_label.update(f"Logged in as: {self.app.session.username}")
        # Initialize calendar to current month
        now = datetime.now(timezone.utc)
        self._cal_year = now.year
        self._cal_month = now.month
        await self.refresh_list()
        await self._refresh_calendar()
        await self._refresh_notebook_select()
        # Start auto-save timer
        self._auto_save_timer = self.set_interval(30, self._auto_save_draft)
        # Restore draft if exists

    async def _refresh_notebook_select(self) -> None:
        """Refresh the notebook dropdown in the New Entry tab."""
        try:
            notebooks = await list_notebooks(self.app.session)
            options = [("(none)", "")]
            for nb_id, name in notebooks:
                options.append((name, str(nb_id)))
            self.nb_select.set_options(options)
            self.nb_select.value = ""
        except Exception:
            pass

    async def on_screen_resume(self) -> None:
        """Restore focus and refresh data when returning from a modal/screen."""
        await self.refresh_list()
        await self._refresh_notebook_select()
        # Restore focus to the list view (most common return target)
        def _restore() -> None:
            try:
                self.set_focus(self.list_view)
            except Exception:
                self.focus_next()
        self.call_after_refresh(_restore)
        draft = await get_draft(self.app.session)
        if draft:
            title, body = draft
            if title or body:
                self.title_in.value = title
                self.body_in.text = body
                self.app.notify("Draft restored")

    async def _auto_save_draft(self) -> None:
        """Timer callback to auto-save new entry draft."""
        if not self.app.session:
            return
        title = self.title_in.value.strip()
        body = self.body_in.text.strip()
        if title or body:
            try:
                await save_draft(self.app.session, title, body)
            except Exception:
                pass  # silent auto-save

    async def refresh_list(self) -> None:
        self.list_view.clear()
        self._total = await count_entries(self.app.session, self._notebook_filter)
        entries = await list_entries_paginated(
            self.app.session,
            sort_asc=self._sort_asc,
            notebook_id=self._notebook_filter,
            limit=PAGE_SIZE,
            offset=self._page * PAGE_SIZE,
        )
        max_page = max(0, (self._total - 1) // PAGE_SIZE)
        sort_dir = "ASC" if self._sort_asc else "DESC"
        nb_label = ""
        if self._notebook_filter is not None:
            nb_label = " [filtered]"
        try:
            self.query_one("#page_info", Static).update(
                f"Page {self._page + 1}/{max_page + 1} ({self._total} entries) [{sort_dir}]{nb_label}"
            )
        except Exception:
            pass

        if not entries:
            self.list_view.append(ListItem(Label("No entries yet. Create one in the New Entry tab!")))
            return
        for eid, created_at, title, is_fav, wc in entries:
            star = " *" if is_fav else ""
            item = ListItem(Label(f"{star} {created_at[:10]} — {title}  [{wc}w]"))
            item.data = eid
            self.list_view.append(item)

    async def _refresh_calendar(self) -> None:
        """Render a text-based calendar for the current month."""
        self.cal_label.update(f"{cal_mod.month_name[self._cal_month]} {self._cal_year}")
        data = await get_calendar_data(self.app.session, self._cal_year, self._cal_month)

        lines = ["Mo Tu We Th Fr Sa Su"]
        cal = cal_mod.monthcalendar(self._cal_year, self._cal_month)
        for week in cal:
            row = []
            for day in week:
                if day == 0:
                    row.append("  ")
                else:
                    date_str = f"{self._cal_year:04d}-{self._cal_month:02d}-{day:02d}"
                    count = data.get(date_str, 0)
                    if count > 0:
                        row.append(f"{day:2d}*" if day < 10 else f"{day}*")
                    else:
                        row.append(f"{day:2d} ")
            lines.append(" ".join(row))
        self.cal_grid.update("\n".join(lines))

    async def on_list_view_selected(self, message: ListView.Selected) -> None:
        eid = getattr(message.item, "data", None)
        if eid is not None:
            await self.app.push_screen(ViewEntryScreen(entry_id=eid))

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "save_entry":
            t = self.title_in.value.strip()
            b = self.body_in.text
            if not t or not b.strip():
                self.app.notify("Title and body required")
                return
            mood = ""
            try:
                mood = self.query_one("#mood_select", Select).value or ""
            except Exception:
                pass
            weather = self.weather_in.value.strip()
            notebook_id = None
            try:
                nb_val = self.nb_select.value
                if nb_val:
                    notebook_id = int(nb_val)
            except Exception:
                pass
            await add_entry(self.app.session, t, b, mood=mood, weather=weather,
                            notebook_id=notebook_id)
            self.title_in.value = ""
            self.body_in.text = ""
            self.weather_in.value = ""
            # Clear draft
            try:
                from cyberjournal.logic import delete_draft as _del_draft
                d = await get_draft(self.app.session)
                if d is not None:
                    from cyberjournal import db as _db
                    row = await _db.get_draft(self.app.session.user_id)
                    if row:
                        await _db.delete_draft(row["id"])
            except Exception:
                pass
            await self.refresh_list()
            self.app.notify("Entry saved")
        elif bid == "do_search":
            q = self.query_in.value.strip()
            self.search_results.clear()
            if q.startswith("tag:"):
                tag = q[4:].strip()
                ids = await search_by_tag(self.app.session, tag)
            else:
                ids = await search_entries(self.app.session, q)
            if not ids:
                self.search_results.append(ListItem(Label("No results.")))
                return
            for eid in ids:
                try:
                    created_at, title, _ = await get_entry(self.app.session, eid)
                except Exception:
                    continue
                li = ListItem(Label(f"{created_at[:10]} — {title}"))
                li.data = eid
                self.search_results.append(li)
        elif bid == "toggle_sort":
            self._sort_asc = not self._sort_asc
            self._page = 0
            await self.refresh_list()
        elif bid == "page_prev":
            if self._page > 0:
                self._page -= 1
                await self.refresh_list()
        elif bid == "page_next":
            max_page = max(0, (self._total - 1) // PAGE_SIZE)
            if self._page < max_page:
                self._page += 1
                await self.refresh_list()
        elif bid == "manage_notebooks":
            def _on_notebook_dismiss(result) -> None:
                if result == -1:
                    return  # no change
                self._notebook_filter = result  # None = show all, int = filter
                self.call_after_refresh(self.refresh_list)
            await self.app.push_screen(
                NotebookModal(current_filter=self._notebook_filter),
                callback=_on_notebook_dismiss,
            )
        elif bid == "open_templates":
            def _on_template(title, body):
                self.title_in.value = title
                self.body_in.text = body
            await self.app.push_screen(TemplateModal(on_select=_on_template))
        elif bid == "cal_prev":
            self._cal_month -= 1
            if self._cal_month < 1:
                self._cal_month = 12
                self._cal_year -= 1
            await self._refresh_calendar()
        elif bid == "cal_next":
            self._cal_month += 1
            if self._cal_month > 12:
                self._cal_month = 1
                self._cal_year += 1
            await self._refresh_calendar()
        elif bid == "open_settings":
            await self.app.push_screen(SettingsModal())
        elif bid == "change_password":
            await self.app.push_screen(ChangePasswordModal())
        elif bid == "enter_world":
            await self.app.push_screen(WorldExplorerScreen())
        elif bid == "export":
            await self.app.push_screen(ExportModal())
        elif bid == "import":
            await self.app.push_screen(ImportModal())
        elif bid == "logout":
            self.app.session = None
            self.dismiss()

    async def action_logout(self) -> None:
        self.app.session = None
        self.dismiss()

    async def action_show_help(self) -> None:
        await self.app.push_screen(HelpModal())


class ViewEntryScreen(Screen):
    """View a single journal entry with map side-by-side."""

    BINDINGS = [
        Binding("escape", "go_back", "Back"),
        Binding("question_mark", "show_help", "Help"),
    ]
    LAYERS = ("bg", "ui")

    def __init__(self, entry_id: int) -> None:
        super().__init__()
        self.entry_id = entry_id

    def compose(self) -> ComposeResult:
        cfg = load_config()
        if cfg.get("ascii_art_enabled", True) and cfg.get("ascii_art"):
            yield Static(str(cfg.get("ascii_art")), id="ascii", classes="layer-bg", markup=False)

        yield Header(classes="layer-ui")

        with Container(id="modal-card", classes="layer-ui"):
            self.title_label = Static("", classes="title")
            yield self.title_label

            self.meta_label = Static("", classes="meta", id="meta-row")
            yield self.meta_label

            with Horizontal(id="entry-row"):
                with Container(id="map-box"):
                    self.map_label = Static("", id="map", markup=False)
                    yield self.map_label
                with Container(id="body-box"):
                    self.body_area = TextArea(id="entry-text", read_only=True)
                    yield self.body_area

            with Horizontal(id="actions"):
                yield Button("Edit", id="edit", classes="-primary")
                yield Button("Fav", id="fav")
                yield Button("Tags", id="tags")
                yield Button("Delete", id="delete")
                yield Button("Back", id="back")

        yield Footer(classes="layer-ui")

    async def on_mount(self) -> None:
        try:
            entry = await get_entry_full(self.app.session, self.entry_id)
        except Exception as exc:
            logger.exception("Failed to load entry")
            self.app.notify(f"Could not load entry: {exc}")
            self.dismiss()
            return

        star = " *" if entry["is_favorite"] else ""
        self.title_label.update(f"{star} {entry['title']}")

        meta_parts = [f"Created: {entry['created_at'][:19]}"]
        meta_parts.append(f"Words: {entry['word_count']}")
        if entry["mood"]:
            meta_parts.append(f"Mood: {entry['mood']}")
        if entry["weather"]:
            meta_parts.append(f"Weather: {entry['weather']}")
        self.meta_label.update("  |  ".join(meta_parts))

        self.body_area.text = entry["body"] or ""

        if entry["map_text"]:
            self.map_label.update(entry["map_text"])
        else:
            self.map_label.update("(no map)")

        self.set_focus(self.body_area)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "back":
            self.dismiss()
        elif bid == "edit":
            await self.app.push_screen(EditEntryModal(self.entry_id))
        elif bid == "delete":
            def _on_delete_result(result: object) -> None:
                if result == "deleted":
                    self.dismiss()
            await self.app.push_screen(ConfirmDeleteModal(self.entry_id), callback=_on_delete_result)
        elif bid == "fav":
            try:
                new_state = await toggle_favorite(self.app.session, self.entry_id)
                star = " *" if new_state else ""
                self.app.notify("Favorited" if new_state else "Unfavorited")
                # Refresh title
                entry = await get_entry_full(self.app.session, self.entry_id)
                self.title_label.update(f"{star} {entry['title']}")
            except Exception as exc:
                self.app.notify(str(exc))
        elif bid == "tags":
            await self.app.push_screen(TagModal(self.entry_id))

    async def on_screen_resume(self) -> None:
        """Restore focus and refresh entry data when returning from a modal."""
        try:
            entry = await get_entry_full(self.app.session, self.entry_id)
            star = " *" if entry["is_favorite"] else ""
            self.title_label.update(f"{star} {entry['title']}")
            self.body_area.text = entry["body"] or ""
        except Exception:
            pass
        self.call_after_refresh(self.set_focus, self.body_area)

    async def action_go_back(self) -> None:
        self.dismiss()

    async def action_show_help(self) -> None:
        await self.app.push_screen(HelpModal())


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

class CyberJournalApp(App):
    """Textual App wrapper."""

    TITLE = "CYBER//JOURNAL"
    CSS_PATH = THEME_CSS_PATH
    BINDINGS = [Binding("question_mark", "show_help", "Help", show=False)]
    session: Optional[SessionKeys] = None

    async def on_mount(self) -> None:
        await init_db()
        cfg = load_config()
        _apply_app_theme(self, str(cfg.get("active_theme", "vt220_green")))
        await self.push_screen(LoginScreen())

    async def action_show_help(self) -> None:
        await self.push_screen(HelpModal())


if __name__ == "__main__":
    import asyncio
    asyncio.run(CyberJournalApp().run_async())
