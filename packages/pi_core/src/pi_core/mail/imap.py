"""IMAP mail source, for a mailbox that accepts app passwords (e.g. a dedicated Gmail account)."""

from __future__ import annotations

import asyncio

from imap_tools import AND, MailBox, U

from pi_core.db import KeyValue
from pi_core.mail.base import MailMessage, message_from_raw


class ImapMailSource:
    def __init__(self, host: str, user: str, password: str, folder: str, state: KeyValue, port: int = 993) -> None:
        self.host, self.port, self.user, self.password, self.folder = host, port, user, password, folder
        self.state = state
        self._key = f"imap.{user}.{folder}"
        self._pending: tuple[str, int] | None = None

    async def fetch_new(self) -> list[MailMessage]:
        return await asyncio.to_thread(self._fetch)

    def _fetch(self) -> list[MailMessage]:
        with MailBox(self.host, self.port).login(self.user, self.password, initial_folder=self.folder) as mb:
            # UIDs are only meaningful together with UIDVALIDITY; if it changes, start over.
            validity = str(mb.folder.status(self.folder, ["UIDVALIDITY"])["UIDVALIDITY"])
            last_uid = int(self.state.get(f"{self._key}.last_uid", "0") or 0)
            if self.state.get(f"{self._key}.uidvalidity") != validity:
                last_uid = 0
            messages = []
            new_last = last_uid
            for msg in mb.fetch(AND(uid=U(last_uid + 1, "*")), mark_seen=False, bulk=20):
                uid = int(msg.uid or 0)
                if uid <= last_uid:  # "N:*" always matches the newest message, even if older than N
                    continue
                messages.append(message_from_raw(msg.obj.as_bytes()))
                new_last = max(new_last, uid)
        self._pending = (validity, new_last)
        return messages

    def commit(self) -> None:
        if self._pending:
            validity, last_uid = self._pending
            self.state.set(f"{self._key}.uidvalidity", validity)
            self.state.set(f"{self._key}.last_uid", str(last_uid))
            self._pending = None
