#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Application entrypoint for CyberJournal.

This file is intentionally minimal. It only boots the Textual UI app.
"""
from __future__ import annotations

import asyncio
from cyberjournal.ui import CyberJournalApp


def main() -> None:
    """Run the Textual application."""
    asyncio.run(CyberJournalApp().run_async())


if __name__ == "__main__":
    main()
