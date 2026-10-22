"""Polling loop: fetch new mail, skip what was already seen, capture bank/merchant emails."""

from __future__ import annotations

import asyncio
import logging

from pi_core.mail.base import MailMessage, MailSource, NeedsLogin

from expenses.capture import Capture
from expenses.store import Store

log = logging.getLogger(__name__)

LOGIN_RETRY_SECONDS = 600
MAX_BACKOFF_SECONDS = 900


def process(messages: list[MailMessage], store: Store, capture: Capture) -> int:
    """Handle a batch of messages. Returns how many were new."""
    new = 0
    for msg in messages:
        if not msg.message_id or store.is_processed(msg.message_id):
            continue
        new += 1
        if capture.config.enabled and capture.matches(msg):
            path = capture.save(msg)
            store.mark_processed(msg, "captured", path)
            log.info("Captured %s from %s -> %s", msg.subject, msg.sender, path)
        else:
            store.mark_processed(msg, "ignored")
            log.debug("Ignored %s from %s", msg.subject, msg.sender)
    return new


async def poll_once(source: MailSource, store: Store, capture: Capture) -> int:
    new = process(await source.fetch_new(), store, capture)
    source.commit()  # only after every message was handled; otherwise the next poll retries them
    return new


async def run_forever(source: MailSource, store: Store, capture: Capture, poll_seconds: int) -> None:
    backoff = poll_seconds
    while True:
        try:
            new = await poll_once(source, store, capture)
            if new:
                log.info("Processed %d new message(s)", new)
            backoff = poll_seconds
            await asyncio.sleep(poll_seconds)
        except NeedsLogin as e:
            log.error("Mail login needed (%s). Run: python -m expenses login", e)
            await asyncio.sleep(LOGIN_RETRY_SECONDS)
        except Exception:
            log.exception("Poll failed; retrying in %ds", backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
