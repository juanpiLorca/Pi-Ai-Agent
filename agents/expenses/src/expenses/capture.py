"""Capture mode: keep redacted copies of bank/merchant emails to use as parser test fixtures."""

from __future__ import annotations

import hashlib
from pathlib import Path

from pi_core.mail.base import MailMessage
from pi_core.mail.redact import redact_raw

from expenses.config import CaptureConfig


class Capture:
    def __init__(self, config: CaptureConfig, samples_dir: Path) -> None:
        self.config = config
        self.samples_dir = samples_dir

    def matches(self, msg: MailMessage) -> bool:
        domain = msg.sender.rpartition("@")[2]
        return any(domain == d or domain.endswith("." + d) for d in self.config.sender_domains)

    def save(self, msg: MailMessage) -> Path:
        domain = msg.sender.rpartition("@")[2]
        digest = hashlib.sha256(msg.message_id.encode()).hexdigest()[:8]
        path = self.samples_dir / domain / f"{msg.received_at:%Y%m%d-%H%M%S}_{digest}.eml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(redact_raw(msg.raw, self.config.redact_names, self.config.redact_emails))
        return path
