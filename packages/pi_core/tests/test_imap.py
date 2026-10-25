import asyncio
import email
import sqlite3
from email.message import EmailMessage
from types import SimpleNamespace

import pytest

from pi_core.db import KeyValue
from pi_core.mail import imap
from pi_core.mail.imap import ImapMailSource


def fake_message(uid: int) -> SimpleNamespace:
    msg = EmailMessage()
    msg["From"] = "Banco de Chile <aviso@bancochile.cl>"
    msg["Subject"] = f"Compra {uid}"
    msg["Date"] = "Thu, 22 Oct 2026 14:35:00 -0300"
    msg["Message-ID"] = f"<{uid}@bancochile.cl>"
    msg.set_content("Compra por $12.990")
    return SimpleNamespace(uid=str(uid), obj=email.message_from_bytes(msg.as_bytes()))


class FakeServer:
    """Stands in for imap_tools.MailBox; records the search criteria it receives."""

    def __init__(self):
        self.uids = [1, 2]
        self.validity = 100
        self.criteria = []
        self.login_args = None

    def __call__(self, host, port, timeout=None):
        return self

    def login(self, user, password, initial_folder):
        self.login_args = (user, password, initial_folder)
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @property
    def folder(self):
        return SimpleNamespace(status=lambda folder, options: {"UIDVALIDITY": self.validity})

    def fetch(self, criteria, mark_seen, bulk):
        assert mark_seen is False  # never change the mailbox
        self.criteria.append(str(criteria))
        start = int(str(criteria).split()[1].split(":")[0])
        # Like a real server, "N:*" returns the newest message even when it is older than N.
        matching = [u for u in self.uids if u >= start] or self.uids[-1:]
        return [fake_message(u) for u in matching]


@pytest.fixture
def server(monkeypatch):
    fake = FakeServer()
    monkeypatch.setattr(imap, "MailBox", fake)
    return fake


def make_source():
    return ImapMailSource("imap.gmail.com", "me@gmail.com", "app-pass", "Gastos", KeyValue(sqlite3.connect(":memory:")))


def ids(messages):
    return [m.message_id for m in messages]


def test_fetches_only_new_uids_after_commit(server):
    source = make_source()
    assert ids(asyncio.run(source.fetch_new())) == ["<1@bancochile.cl>", "<2@bancochile.cl>"]
    assert server.login_args == ("me@gmail.com", "app-pass", "Gastos")
    source.commit()

    assert asyncio.run(source.fetch_new()) == []  # server returns UID 2 for "3:*"; it's skipped
    assert server.criteria[-1] == "(UID 3:*)"

    server.uids.append(3)
    assert ids(asyncio.run(source.fetch_new())) == ["<3@bancochile.cl>"]


def test_without_commit_messages_are_returned_again(server):
    source = make_source()
    asyncio.run(source.fetch_new())
    assert len(asyncio.run(source.fetch_new())) == 2


def test_uidvalidity_change_restarts_from_scratch(server):
    source = make_source()
    asyncio.run(source.fetch_new())
    source.commit()
    server.validity = 200
    assert len(asyncio.run(source.fetch_new())) == 2
