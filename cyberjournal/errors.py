# -*- coding: utf-8 -*-
"""Domain exception types for CyberJournal."""
from __future__ import annotations


class CyberJournalError(Exception):
    """Base exception for all CyberJournal errors."""


class DatabaseError(CyberJournalError):
    """An error originating from the database layer."""


class DuplicateUserError(CyberJournalError):
    """Raised when attempting to register a username that already exists."""


class EntryNotFoundError(CyberJournalError):
    """Raised when an entry lookup returns no result."""
