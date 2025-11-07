# cyberjournal_app.py
"""
Working Textual TUI: cyberpunk-styled encrypted journal with
- Login (with Settings / Create User / Reset Password modals from login)
- Post-login journal (Browse, New Entry, Search, Account)
- SQLite storage + per-user AES-GCM row encryption + blind-index search
- Theme & ASCII-art persisted to ~/.config/cyberjournal/config.json
- Valid Textual CSS (no invalid units; all literal {{ }} escaped)

Run:
    pip install textual==0.49.0 rich aiosqlite cryptography argon2-cffi
    python cyberjournal_app.py
"""
from __future__ import annotations

import asyncio
import aiosqlite
import json
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Set

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes, hmac
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.widgets import (
    Header, Footer, Button, Static, Input, Label, ListView, ListItem,
    TextArea, TabPane, TabbedContent
)
from textual.screen import Screen, ModalScreen

# ------------------
# Config persistence
# ------------------

APP_NAME = "cyberjournal"

DEFAULT_CONFIG: Dict[str, object] = {
    "active_theme": "vt220_green",  # vt220_green | as400_amber | vector_neon
    "themes": {
        "vt220_green": {"fg": "#00ff88", "bg": "#001100", "accent": "#11dd88", "border": "#00cc66", "title": "#24ffa8"},
        "as400_amber": {"fg": "#ffb966", "bg": "#1a0a00", "accent": "#e6a555", "border": "#c78a3d", "title": "#ffd391"},
        "vector_neon": {"fg": "#39ff14", "bg": "#000900", "accent": "#1aff66", "border": "#10d650", "title": "#7dffb0"},
    },
    "ascii_art_enabled": True,
    "ascii_art": (
        "   ____      _               ___        _                 \\n"
        "  / ___|__ _| |__   ___ _ __|_ _|_ __  | | ___  _   _    \\n"
        " | |   / _` | '_ \\ / _ \\ '__|| || '_ \\ | |/ _ \\| | | |   \\n"
        " | |__| (_| | |_) |  __/ |   | || | | || | (_) | |_| |   \\n"
        "  \\____\\__,_|_.__/ \\___|_|  |___|_| |_||_|\\___/ \\__, |   \\n"
        "                                                |___/    \\n"
    ),
}

def _config_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("APPDATA", os.path.expanduser("~\\AppData\\Roaming"))
        return Path(base) / APP_NAME
    base = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    return Path(base) / APP_NAME

def _config_path() -> Path:
    return _config_dir() / "config.json"

def load_config() -> Dict[str, object]:
    path = _config_path()
    if not path.exists():
        save_config(DEFAULT_CONFIG)
        return json.loads(json.dumps(DEFAULT_CONFIG))
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    merged.update(data)
    merged["themes"].update(data.get("themes", {}))
    return merged

def save_config(cfg: Dict[str, object]) -> None:
    _config_dir().mkdir(parents=True, exist_ok=True)
    with _config_path().open("w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)

# -------------
# CSS generator
# -------------

def build_css(theme: Dict[str, str]) -> str:
    bg = theme["bg"]; fg = theme["fg"]; accent = theme["accent"]; border = theme["border"]; title = theme["title"]
    # IMPORTANT: All literal braces are doubled ({{ and }}) to avoid f-string evaluation.
    return f"""
/* Global */
* {{ background: {bg}; color: {fg}; }}
Screen {{ align: center middle; }}

Header, Footer {{
  background: {bg};
  color: {accent};
  text-style: bold;
  border: heavy {border};
  height: 3;
}}

/* Base framed window that fills the screen behind modal */
#base-frame {{
  width: 96%;
  height: 90%;
  border: heavy {border};
  background: {bg};
  margin: 0;              /* 'auto' not supported in Textual CSS */
  padding: 1 2;
}}

/* ASCII art background inside base frame */
#ascii {{
  width: 100%;
  /* No 'height' property to avoid engine complaints; let layout size it */
  color: {accent};
}}

/* Centered modal window */
#modal-card {{
  width: 84w;            /* Textual units: %, w (columns), h (rows), vh, vw, fr */
  max-width: 90%;
  border: heavy {border};
  background: {bg};
  padding: 1 2;
}}

Static.title {{ color: {title}; text-style: bold; }}
Static.hint  {{ color: {accent}; }}
Static.error {{ color: {accent}; }}

Button {{
  background: {bg};
  color: {fg};
  border: round {border};
  padding: 0 2;
  height: 3;
  margin: 1 1 0 0;
}}
Button.-primary {{ border: heavy {title}; color: {title}; }}
Button:hover {{ background: {bg}; color: {title}; }}

Input, TextArea {{
  background: {bg};
  color: {fg};
  border: round {border};
}}

ListView {{ border: round {border}; }}
"""

# -------------------------------
# Crypto helpers & key handling
# -------------------------------

PH = PasswordHasher(time_cost=2, memory_cost=102400, parallelism=8, hash_len=32, salt_len=16)

SCRYPT_N = 2 ** 14
SCRYPT_R = 8
SCRYPT_P = 1
KEK_LEN = 32
DEK_LEN = 32

HKDF_INFO_ENC = b"cyberjournal/enc-key"
HKDF_INFO_HMAC = b"cyberjournal/search-key"
NONCE_LEN = 12

TOKEN_SPLIT_RE = re.compile(r"[\\W_]+", re.UNICODE)

def _scrypt_kdf(password: str, salt: bytes, length: int = KEK_LEN) -> bytes:
    return Scrypt(salt=salt, length=length, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P).derive(password.encode("utf-8"))

def hkdf_derive(key_material: bytes, info: bytes, length: int = 32) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=None, info=info).derive(key_material)

def aesgcm_encrypt(key: bytes, plaintext: bytes, aad: Optional[bytes] = None) -> Tuple[bytes, bytes]:
    nonce = secrets.token_bytes(NONCE_LEN)
    ct = AESGCM(key).encrypt(nonce, plaintext, aad)
    return nonce, ct

def aesgcm_decrypt(key: bytes, nonce: bytes, ciphertext: bytes, aad: Optional[bytes] = None) -> bytes:
    return AESGCM(key).decrypt(nonce, ciphertext, aad)

def normalize_tokens(text: str) -> List[str]:
    return [p for p in TOKEN_SPLIT_RE.split(text.lower()) if p]

def hmac_token(search_key: bytes, token: str) -> bytes:
    h = hmac.HMAC(search_key, hashes.SHA256()); h.update(token.encode("utf-8")); return h.finalize()

# -----------------------------
# Database schema & operations
# -----------------------------

DB_PATH = os.environ.get("CYBERJOURNAL_DB", "journal_encrypted.sqlite3")

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT UNIQUE NOT NULL,
    pwd_hash        TEXT NOT NULL,
    kek_salt        BLOB NOT NULL,
    dek_wrapped     BLOB NOT NULL,
    dek_wrap_nonce  BLOB NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    created_at      TEXT NOT NULL,
    title_nonce     BLOB NOT NULL,
    title_ct        BLOB NOT NULL,
    body_nonce      BLOB NOT NULL,
    body_ct         BLOB NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS entry_terms (
    entry_id        INTEGER NOT NULL,
    term_hash       BLOB NOT NULL,
    UNIQUE(entry_id, term_hash),
    FOREIGN KEY (entry_id) REFERENCES entries(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_terms_hash ON entry_terms(term_hash);
CREATE INDEX IF NOT EXISTS idx_entries_user ON entries(user_id);
"""

async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA_SQL)
        await db.commit()

@dataclass
class SessionKeys:
    user_id: int
    username: str
    dek: bytes
    enc_key: bytes
    search_key: bytes

async def _insert_user(username: str, pwd_hash: str, kek_salt: bytes, dek_wrapped: bytes, dek_wrap_nonce: bytes, created_at: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO users (username, pwd_hash, kek_salt, dek_wrapped, dek_wrap_nonce, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (username, pwd_hash, kek_salt, dek_wrapped, dek_wrap_nonce, created_at),
        )
        await db.commit()

async def register_user(username: str, password: str) -> None:
    created_at = datetime.utcnow().isoformat()
    pwd_hash = PH.hash(password)
    dek = secrets.token_bytes(DEK_LEN)
    kek_salt = secrets.token_bytes(16)
    kek = _scrypt_kdf(password, kek_salt, KEK_LEN)
    wrap_key = hkdf_derive(kek, b"wrap-key", 32)
    nonce, wrapped = aesgcm_encrypt(wrap_key, dek, aad=username.encode())
    await _insert_user(username, pwd_hash, kek_salt, wrapped, nonce, created_at)

async def login_user(username: str, password: str) -> SessionKeys:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE username = ?", (username,))
        row = await cur.fetchone(); await cur.close()
    if not row:
        raise ValueError("User not found")
    try:
        PH.verify(row["pwd_hash"], password)
    except VerifyMismatchError as exc:
        raise ValueError("Invalid password") from exc
    kek = _scrypt_kdf(password, row["kek_salt"], KEK_LEN)
    wrap_key = hkdf_derive(kek, b"wrap-key", 32)
    dek = aesgcm_decrypt(wrap_key, row["dek_wrap_nonce"], row["dek_wrapped"], aad=row["username"].encode())
    enc_key = hkdf_derive(dek, HKDF_INFO_ENC, 32)
    search_key = hkdf_derive(dek, HKDF_INFO_HMAC, 32)
    return SessionKeys(user_id=row["id"], username=row["username"], dek=dek, enc_key=enc_key, search_key=search_key)

async def insert_entry(sess: SessionKeys, title: str, body: str) -> int:
    created_at = datetime.utcnow().isoformat()
    t_nonce, t_ct = aesgcm_encrypt(sess.enc_key, title.encode(), aad=sess.username.encode())
    b_nonce, b_ct = aesgcm_encrypt(sess.enc_key, body.encode(), aad=sess.username.encode())
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO entries (user_id, created_at, title_nonce, title_ct, body_nonce, body_ct) VALUES (?, ?, ?, ?, ?, ?)",
            (sess.user_id, created_at, t_nonce, t_ct, b_nonce, b_ct),
        )
        entry_id = cur.lastrowid
        terms: Set[str] = set(normalize_tokens(title) + normalize_tokens(body))
        pairs = [(entry_id, hmac_token(sess.search_key, t)) for t in terms]
        await db.executemany("INSERT OR IGNORE INTO entry_terms (entry_id, term_hash) VALUES (?, ?)", pairs)
        await db.commit()
        return int(entry_id)

async def list_entries(sess: SessionKeys) -> List[Tuple[int, str, str]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id, created_at, title_nonce, title_ct FROM entries WHERE user_id = ? ORDER BY created_at DESC",
            (sess.user_id,),
        )
        rows = await cur.fetchall(); await cur.close()
    out: List[Tuple[int, str, str]] = []
    for r in rows:
        title = aesgcm_decrypt(sess.enc_key, r["title_nonce"], r["title_ct"], aad=sess.username.encode()).decode()
        out.append((r["id"], r["created_at"], title))
    return out

async def get_entry(sess: SessionKeys, entry_id: int) -> Tuple[str, str, str]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT created_at, title_nonce, title_ct, body_nonce, body_ct FROM entries WHERE id = ? AND user_id = ?",
            (entry_id, sess.user_id),
        )
        row = await cur.fetchone(); await cur.close()
    if not row:
        raise ValueError("Entry not found")
    title = aesgcm_decrypt(sess.enc_key, row["title_nonce"], row["title_ct"], aad=sess.username.encode()).decode()
    body = aesgcm_decrypt(sess.enc_key, row["body_nonce"], row["body_ct"], aad=sess.username.encode()).decode()
    return row["created_at"], title, body

async def search_entries(sess: SessionKeys, query: str) -> List[int]:
    tokens = [t for t in normalize_tokens(query) if t]
    if not tokens:
        return []
    async with aiosqlite.connect(DB_PATH) as db:
        id_sets: List[Set[int]] = []
        for token in tokens:
            th = hmac_token(sess.search_key, token)
            cur = await db.execute("SELECT entry_id FROM entry_terms WHERE term_hash = ?", (th,))
            rows = await cur.fetchall(); await cur.close()
            id_sets.append({int(r[0]) for r in rows})
    return sorted(set.intersection(*id_sets), reverse=True) if id_sets else []

# ---------
# UI: Modals
# ---------

class SettingsModal(ModalScreen[None]):
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
                id="modal-card",
            ),
            Static("ASCII ART (appears behind modal)", classes="hint"),
            Input(value="on" if ascii_enabled else "off", id="ascii_toggle"),
            TextArea(value=ascii_text, id="ascii_text", placeholder="ASCII art here..."),
            Horizontal(Button("Save", id="save", classes="-primary"), Button("Close", id="close")),
            id="modal-card",
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
            self.app.pop_screen()
            return
        elif bid == "close":
            self.app.pop_screen(); return
        save_config(cfg)
        self.app.pop_screen(); self.app.push_screen(SettingsModal())

class CreateUserModal(ModalScreen[None]):
    def compose(self) -> ComposeResult:
        yield Container(
            Static("CREATE USER", classes="title"),
            Input(placeholder="username", id="u"),
            Input(placeholder="password", password=True, id="p"),
            Input(placeholder="confirm", password=True, id="c"),
            Horizontal(Button("Create", id="create", classes="-primary"), Button("Close", id="close")),
            id="modal-card",
        )

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "create":
            u = self.query_one("#u", Input).value.strip()
            p = self.query_one("#p", Input).value
            c = self.query_one("#c", Input).value
            if not u or not p or p != c:
                return
            try:
                await register_user(u, p)
                self.app.pop_screen()
            except Exception:
                pass
        elif event.button.id == "close":
            self.app.pop_screen()

class ResetPasswordModal(ModalScreen[None]):
    def compose(self) -> ComposeResult:
        yield Container(
            Static("RESET PASSWORD", classes="title"),
            Input(placeholder="username", id="u"),
            Input(placeholder="current password", password=True, id="p0"),
            Input(placeholder="new password", password=True, id="p1"),
            Input(placeholder="confirm new", password=True, id="p2"),
            Horizontal(Button("Reset", id="reset", classes="-primary"), Button("Close", id="close")),
            id="modal-card",
        )

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "reset":
            u = self.query_one("#u", Input).value.strip()
            p0 = self.query_one("#p0", Input).value
            p1 = self.query_one("#p1", Input).value
            p2 = self.query_one("#p2", Input).value
            if not u or not p0 or not p1 or p1 != p2:
                return
            try:
                await reset_password(u, p0, p1)
                self.app.pop_screen()
            except Exception:
                pass
        elif event.button.id == "close":
            self.app.pop_screen()

# -------------
# UI: Screens
# -------------

class LoginScreen(Screen):
    BINDINGS = [Binding("escape", "quit", "Quit")]
    def compose(self) -> ComposeResult:
        cfg = load_config()
        with Container(id="base-frame"):
            if cfg.get("ascii_art_enabled", True) and cfg.get("ascii_art"):
                yield Static(str(cfg.get("ascii_art")), id="ascii")
        yield Header()
        yield Container(
            Static("LOGIN", classes="title"),
            Input(placeholder="username", id="username"),
            Input(placeholder="password", password=True, id="password"),
            Horizontal(Button("Login", id="do_login", classes="-primary"), Button("Exit", id="exit")),
            Horizontal(Button("Settings", id="open_settings"), Button("Create User", id="open_create"), Button("Reset Password", id="open_reset")),
            id="modal-card",
        )
        yield Footer()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "do_login":
            u = self.query_one("#username", Input).value.strip()
            p = self.query_one("#password", Input).value
            try:
                sess = await login_user(u, p)
                self.app.session = sess
                await self.app.push_screen(JournalHomeScreen())
                await self.app.remove_screen(self)
            except Exception:
                pass
        elif bid == "exit":
            self.app.exit()
        elif bid == "open_settings":
            self.app.push_screen(SettingsModal())
        elif bid == "open_create":
            self.app.push_screen(CreateUserModal())
        elif bid == "open_reset":
            self.app.push_screen(ResetPasswordModal())

class JournalHomeScreen(Screen):
    BINDINGS = [Binding("escape", "app.pop_screen", "Back")]
    def compose(self) -> ComposeResult:
        cfg = load_config()
        with Container(id="base-frame"):
            if cfg.get("ascii_art_enabled", True) and cfg.get("ascii_art"):
                yield Static(str(cfg.get("ascii_art")), id="ascii")
        yield Header()
        with Container(id="modal-card"):
            yield Static("CYBER//JOURNAL", classes="title")
            with TabbedContent():
                with TabPane("Browse"):
                    self.list_view = ListView(); yield self.list_view
                with TabPane("New Entry"):
                    self.title_in = Input(placeholder="title")
                    self.body_in = TextArea(placeholder="body (Ctrl+Enter to save)")
                    yield self.title_in; yield self.body_in
                    yield Button("Save Entry", id="save_entry", classes="-primary")
                with TabPane("Search"):
                    self.query_in = Input(placeholder="search tokens (AND)")
                    yield self.query_in
                    yield Button("Search", id="do_search")
                    self.search_results = ListView(); yield self.search_results
                with TabPane("Account"):
                    yield Static(lambda: f"Logged in as: {self.app.session.username}")
                    Horizontal(Button("Settings", id="open_settings"), Button("Logout", id="logout"))
        yield Footer()

    async def on_mount(self) -> None:
        await self.refresh_list()

    async def refresh_list(self) -> None:
        self.list_view.clear()
        entries = await list_entries(self.app.session)
        for eid, created_at, title in entries:
            item = ListItem(Label(f"{created_at} — {title}")); item.data = eid; self.list_view.append(item)

    async def on_list_view_selected(self, message: ListView.Selected) -> None:
        await self.app.push_screen(ViewEntryScreen(entry_id=message.item.data))

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "save_entry":
            t = self.title_in.value.strip(); b = self.body_in.text
            if not t or not b.strip():
                return
            await insert_entry(self.app.session, t, b)
            self.title_in.value = ""; self.body_in.text = ""; await self.refresh_list()
        elif bid == "do_search":
            q = self.query_in.value; ids = await search_entries(self.app.session, q)
            self.search_results.clear()
            if not ids: self.search_results.append(ListItem(Label("No results."))); return
            for eid in ids:
                created_at, title, _ = await get_entry(self.app.session, eid)
                li = ListItem(Label(f"{created_at} — {title}")); li.data = eid; self.search_results.append(li)
        elif bid == "open_settings":
            self.app.push_screen(SettingsModal())
        elif bid == "logout":
            self.app.session = None; await self.app.pop_screen()

class ViewEntryScreen(Screen):
    def __init__(self, entry_id: int) -> None:
        super().__init__(); self.entry_id = entry_id
    def compose(self) -> ComposeResult:
        cfg = load_config()
        with Container(id="base-frame"):
            if cfg.get("ascii_art_enabled", True) and cfg.get("ascii_art"):
                yield Static(str(cfg.get("ascii_art")), id="ascii")
        yield Header()
        with Container(id="modal-card"):
            self.title = Static("", classes="title"); yield self.title
            self.meta = Static("", classes="hint"); yield self.meta
            self.body = Static(""); yield self.body
            yield Button("Back", id="back")
        yield Footer()
    async def on_mount(self) -> None:
        created_at, title, body = await get_entry(self.app.session, self.entry_id)
        self.title.update(title); self.meta.update(f"Created: {created_at}"); self.body.update(body)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back": self.app.pop_screen()

# -------
# App
# -------

class CyberJournalApp(App):
    TITLE = "CYBER//JOURNAL"
    session: Optional[SessionKeys] = None

    def _apply_theme(self) -> None:
        cfg = load_config(); theme = cfg["themes"][cfg["active_theme"]]  # type: ignore[index]
        self.stylesheet.add_source(build_css(theme))

    async def on_mount(self) -> None:
        await init_db()
        self._apply_theme()
        await self.push_screen(LoginScreen())

if __name__ == "__main__":
    asyncio.run(CyberJournalApp().run_async())
