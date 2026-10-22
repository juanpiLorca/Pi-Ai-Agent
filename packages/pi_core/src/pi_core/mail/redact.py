"""Redact personal data from notification emails so they can be shared as parser fixtures.

Keeps what parsers need (amounts, merchants, dates, HTML structure) and replaces
RUTs, card/account numbers (keeping the last 4 digits), names and email
addresses with placeholders. Non-text parts (logos, attachments) are dropped.

Only uses the standard library, so tools/redact_eml.py can run it without installing anything.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser

KEPT_HEADERS = ("From", "Subject", "Date", "Content-Language")

RUT_RE = re.compile(r"\b\d{1,2}\.?\d{3}\.?\d{3}-[\dkK]\b")
# 8+ digits, optionally separated by spaces/dashes (card or account numbers).
# Amounts like $1.234.567 use dots and are left alone.
LONG_NUMBER_RE = re.compile(r"\b\d(?:[ -]?\d){7,}\b")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
URL_QUERY_RE = re.compile(r"(https?://[^\s\"'<>?#]+)[?#][^\s\"'<>]*")


def _mask_number(match: re.Match[str]) -> str:
    digits = re.sub(r"\D", "", match.group())
    return "X" * (len(digits) - 4) + digits[-4:]


def redact_text(text: str, names: Iterable[str], emails: Iterable[str]) -> str:
    text = RUT_RE.sub("11.111.111-1", text)
    text = LONG_NUMBER_RE.sub(_mask_number, text)
    text = URL_QUERY_RE.sub(r"\1", text)  # tracking links often carry user ids
    for addr in emails:
        text = re.sub(re.escape(addr), "usuario@example.com", text, flags=re.IGNORECASE)
    # Full names first, then each word, so partial mentions ("Estimado Juan") go too.
    tokens = sorted({t for n in names for t in [n, *n.split()] if len(t) >= 3}, key=len, reverse=True)
    for token in tokens:
        text = re.sub(rf"\b{re.escape(token)}\b", "NOMBRE", text, flags=re.IGNORECASE)
    return text


def redact_message(msg: EmailMessage, names: Iterable[str] = (), emails: Iterable[str] = ()) -> EmailMessage:
    names = list(names)
    # The recipient is always the account owner, so their address is always removed.
    emails = [*emails, *EMAIL_RE.findall(str(msg.get("To", "")))]

    plain = html = None
    for part in msg.walk():
        if part.is_attachment():
            continue
        ctype = part.get_content_type()
        if ctype == "text/plain" and plain is None:
            plain = part.get_content()
        elif ctype == "text/html" and html is None:
            html = part.get_content()

    out = EmailMessage()
    for header in KEPT_HEADERS:
        if msg[header]:
            value = str(msg[header])
            out[header] = value if header == "From" else redact_text(value, names, emails)
    out["To"] = "usuario@example.com"
    msg_id = str(msg.get("Message-ID", ""))
    out["Message-ID"] = f"<{hashlib.sha256(msg_id.encode()).hexdigest()[:16]}@redacted>"

    if plain is not None:
        out.set_content(redact_text(plain, names, emails))
        if html is not None:
            out.add_alternative(redact_text(html, names, emails), subtype="html")
    elif html is not None:
        out.set_content(redact_text(html, names, emails), subtype="html")
    return out


def redact_raw(raw: bytes, names: Iterable[str] = (), emails: Iterable[str] = ()) -> bytes:
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    return redact_message(msg, names, emails).as_bytes()
