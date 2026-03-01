# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

CyberJournal is a terminal-first encrypted personal journal with a retro TUI, blind-index search, and procedurally generated ASCII map previews. Python + Textual framework, fully offline.

## Running

```bash
# Install dependencies (use a venv)
pip install -r requirements.txt

# Run the app
python app.py

# Custom database location
CYBERJOURNAL_DB=/path/to/db.sqlite3 python app.py

# Run legacy database migrations
python db_migrate.py
```

No test framework or linter is currently configured.

## Architecture

Layered design with strict separation of concerns:

- **`app.py`** — Minimal entry point, runs `CyberJournalApp` via `asyncio.run()`
- **`cyberjournal/crypto.py`** — Stateless cryptographic primitives (Scrypt KDF, AES-GCM, HKDF, HMAC blind indexing). No persistent state; session state lives in `SessionKeys` dataclass.
- **`cyberjournal/db.py`** — Async SQLite layer (aiosqlite). Schema defined as SQL string with idempotent migrations via `ALTER TABLE`. Tables: `users`, `entries` (encrypted fields), `entry_terms` (blind index hashes).
- **`cyberjournal/logic.py`** — Business logic composing db + crypto. Config management (JSON in `~/.config/cyberjournal/`), auth flows, entry CRUD, blind-index search.
- **`cyberjournal/ui.py`** — Textual TUI screens and modal dialogs. Consumes logic layer only.
- **`cyberjournal/map.py`** — Standalone procedural ASCII/UTF map generator. Deterministic: same text always produces the same map.
- **`cyberjournal/theme.css`** — Textual CSS with 3 themes (VT220 Green, AS/400 Amber, Vector Neon) applied via class toggling.

## Encryption Architecture

```
Password → Scrypt(salt) → KEK → HKDF("wrap-key") → wraps DEK (AES-GCM)
DEK → HKDF("cyberjournal/enc-key") → enc_key (encrypts entries)
DEK → HKDF("cyberjournal/search-key") → search_key (HMAC blind index tokens)
```

- Each encrypted field (title, body, map) has its own random 12-byte nonce
- AAD = username for all AES-GCM operations
- Password reset via security question wipes all entries (by design)
- Search uses HMAC-SHA256 blind indexing — only hashes stored, never plaintext keywords

## Key Patterns

- All database operations are async; all crypto is sync (CPU-bound)
- Logic layer is UI-agnostic — could support alternative frontends
- Config merges file values with defaults on load
- Database auto-creates on first run; migrations are idempotent
