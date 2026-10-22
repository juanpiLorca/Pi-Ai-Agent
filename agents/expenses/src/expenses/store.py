"""The expenses agent's SQLite database."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from pi_core.db import connect, migrate
from pi_core.mail.base import MailMessage

MIGRATIONS = [
    """
    CREATE TABLE processed_emails (
        message_id   TEXT PRIMARY KEY,
        sender       TEXT NOT NULL,
        subject      TEXT NOT NULL,
        received_at  TEXT NOT NULL,
        processed_at TEXT NOT NULL,
        result       TEXT NOT NULL,  -- captured | ignored
        sample_path  TEXT
    );
    """,
]


class Store:
    def __init__(self, path: Path) -> None:
        self.conn: sqlite3.Connection = connect(path)
        migrate(self.conn, MIGRATIONS)

    def is_processed(self, message_id: str) -> bool:
        return self.conn.execute("SELECT 1 FROM processed_emails WHERE message_id = ?", (message_id,)).fetchone() is not None

    def mark_processed(self, msg: MailMessage, result: str, sample_path: Path | None = None) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO processed_emails VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    msg.message_id,
                    msg.sender,
                    msg.subject,
                    msg.received_at.isoformat(),
                    datetime.now(UTC).isoformat(),
                    result,
                    str(sample_path) if sample_path else None,
                ),
            )
