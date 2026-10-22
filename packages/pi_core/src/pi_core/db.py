"""SQLite helpers: connection setup, ordered migrations, and a small key-value store."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def migrate(conn: sqlite3.Connection, migrations: Sequence[str]) -> None:
    """Apply migrations[user_version:] in order. Never edit a migration once released; append a new one."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for number, script in enumerate(migrations[version:], start=version + 1):
        with conn:
            conn.executescript(script)
            conn.execute(f"PRAGMA user_version = {number}")


class KeyValue:
    """Persistent agent state (sync cursors, flags) stored in the agent's own database."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        with conn:
            conn.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)")

    def get(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def set(self, key: str, value: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def delete(self, key: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM kv WHERE key = ?", (key,))
