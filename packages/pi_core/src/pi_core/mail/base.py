"""Mail source abstraction: every backend returns full raw MIME messages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime
from typing import Protocol


class NeedsLogin(Exception):
    """The mail backend has no valid credentials; a person has to log in again."""


@dataclass(frozen=True)
class MailMessage:
    message_id: str  # RFC 5322 Message-ID, stable across backends; used for dedup
    sender: str  # bare address, lowercase
    subject: str
    received_at: datetime  # timezone-aware
    raw: bytes  # full MIME, as an .eml file would contain

    def parsed(self) -> EmailMessage:
        return BytesParser(policy=policy.default).parsebytes(self.raw)


def message_from_raw(raw: bytes, received_at: datetime | None = None) -> MailMessage:
    msg = BytesParser(policy=policy.default).parsebytes(raw, headersonly=True)
    if received_at is None:
        try:
            received_at = parsedate_to_datetime(str(msg["Date"]))
        except (TypeError, ValueError):
            received_at = datetime.now(UTC)
    return MailMessage(
        message_id=str(msg.get("Message-ID", "")).strip(),
        sender=parseaddr(str(msg.get("From", "")))[1].lower(),
        subject=str(msg.get("Subject", "")),
        received_at=received_at,
        raw=raw,
    )


class MailSource(Protocol):
    async def fetch_new(self) -> list[MailMessage]:
        """Return messages that arrived since the last commit(). Calling it again without
        commit() returns them again, so a failure while handling them loses nothing."""
        ...

    def commit(self) -> None:
        """Mark everything returned by the last fetch_new() as handled."""
        ...
