import asyncio
from email.message import EmailMessage

import pytest

from pi_core.mail.base import MailMessage, message_from_raw

from expenses.agent import poll_once
from expenses.capture import Capture
from expenses.config import CaptureConfig
from expenses.store import Store


def make_mail(sender: str, msg_id: str, body: str = "Compra por $12.990") -> MailMessage:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "Juan Lorca <juan.lorca@uc.cl>"
    msg["Subject"] = "Aviso de compra"
    msg["Date"] = "Thu, 22 Oct 2026 14:35:00 -0300"
    msg["Message-ID"] = msg_id
    msg.set_content(body)
    return message_from_raw(msg.as_bytes())


class FakeSource:
    def __init__(self, batches):
        self.batches = list(batches)
        self.commits = 0

    async def fetch_new(self):
        return self.batches[0] if self.batches else []

    def commit(self):
        self.commits += 1
        if self.batches:
            self.batches.pop(0)


def test_captures_bank_mail_ignores_others_and_dedups(tmp_path):
    store = Store(tmp_path / "expenses.db")
    capture = Capture(CaptureConfig(redact_names=["Juan Lorca"]), tmp_path / "samples")
    bank = make_mail("Banco de Chile <enviodigital@bancochile.cl>", "<1@bancochile.cl>", "Hola Juan Lorca, $12.990")
    other = make_mail("news@example.com", "<2@example.com>")
    source = FakeSource([[bank, other], [bank]])  # second poll repeats the bank email

    assert asyncio.run(poll_once(source, store, capture)) == 2
    assert asyncio.run(poll_once(source, store, capture)) == 0

    samples = list((tmp_path / "samples").rglob("*.eml"))
    assert [p.parent.name for p in samples] == ["bancochile.cl"]
    content = samples[0].read_text()
    assert "$12.990" in content
    assert "Juan" not in content and "juan.lorca@uc.cl" not in content
    rows = store.conn.execute("SELECT result FROM processed_emails ORDER BY message_id").fetchall()
    assert [r["result"] for r in rows] == ["captured", "ignored"]


def test_subdomain_matches_but_lookalike_does_not(tmp_path):
    capture = Capture(CaptureConfig(), tmp_path)
    assert capture.matches(make_mail("noreply@mail.rappi.com", "<a@x>"))
    assert not capture.matches(make_mail("phish@notbancochile.cl", "<b@x>"))


def test_failed_processing_does_not_commit_so_messages_are_retried(tmp_path):
    store = Store(tmp_path / "expenses.db")
    samples = tmp_path / "samples"
    samples.write_text("not a directory")  # saving a sample will fail
    capture = Capture(CaptureConfig(), samples)
    source = FakeSource([[make_mail("aviso@bice.cl", "<1@bice.cl>")]])

    with pytest.raises(OSError):
        asyncio.run(poll_once(source, store, capture))
    assert source.commits == 0 and not store.is_processed("<1@bice.cl>")

    samples.unlink()
    assert asyncio.run(poll_once(source, store, capture)) == 1
    assert source.commits == 1
