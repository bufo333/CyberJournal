# =====================
# File: app.py
# =====================
"""Executable entry point for Cyberjournal.

Usage
-----
    python app.py

Environment
-----------
- ``CYBERJOURNAL_DB``: Path to the SQLite database file (default: journal_encrypted.sqlite3)
"""
from __future__ import annotations

import asyncio

from cyberjournal.ui import CyberJournalApp


if __name__ == "__main__":
    asyncio.run(CyberJournalApp().run_async())
