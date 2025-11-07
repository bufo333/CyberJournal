# Cyberjournal — Textual TUI, Encrypted SQLite
# Project layout (single-canvas multi-file view)
#
# cyberjournal/
#   __init__.py
#   crypto.py
#   db.py
#   logic.py
#   ui.py
#   theme.css
# app.py
#
# Notes
# -----
# • Follows PEP 8/PEP 257 with type hints, module/file headers, and clear separation of concerns.
# • Crypto helpers are isolated in crypto.py; SQL and schema live in db.py; orchestration/business rules in logic.py; UI in ui.py; CSS theme in theme.css.
# • Search uses a deterministic blind index (HMAC over normalized tokens) derived from a per-user DEK; equality/AND only.
# • To run: `pip install textual rich aiosqlite cryptography argon2-cffi` then `python app.py`.
# • Set DB path via env `CYBERJOURNAL_DB` if desired.

