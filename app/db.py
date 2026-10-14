"""The console's SQLite database, shared by every store (accounts, answers, sources, connectors, keys)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def connect(path: Path) -> Iterator[sqlite3.Connection]:
    """One short connection per operation: safe from any thread, and nothing stays locked.

    Rows read like dicts; the block commits on success and rolls back on error.
    """
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        with db:
            yield db
    finally:
        db.close()
