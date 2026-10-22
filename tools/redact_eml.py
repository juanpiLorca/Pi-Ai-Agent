"""Redact personal data from bank/merchant notification emails (.eml).

Keeps what parsers need (amounts, merchants, dates, HTML structure) and replaces
RUTs, card/account numbers (keeping the last 4 digits), names and email
addresses with placeholders. Non-text parts (logos, attachments) are dropped.

Usage:
    python tools/redact_eml.py mail1.eml mail2.eml --name "Juan Lorca" --email juan.lorca@uc.cl

Writes <file>.redacted.eml next to each input. Review the output before sharing.
"""

from __future__ import annotations

import argparse
import hashlib
import re
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path

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


def redact_text(text: str, names: list[str], emails: list[str]) -> str:
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


def redact_message(msg: EmailMessage, names: list[str], emails: list[str]) -> EmailMessage:
    plain = html = None
    for part in msg.walk():
        ctype = part.get_content_type()
        if part.is_attachment():
            continue
        if ctype == "text/plain" and plain is None:
            plain = part.get_content()
        elif ctype == "text/html" and html is None:
            html = part.get_content()

    out = EmailMessage()
    for header in KEPT_HEADERS:
        if msg[header]:
            out[header] = redact_text(str(msg[header]), names, emails) if header != "From" else msg[header]
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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--name", action="append", default=[], help="name to remove (repeatable)")
    ap.add_argument("--email", action="append", default=[], help="email address to remove (repeatable)")
    args = ap.parse_args()

    for path in args.files:
        with path.open("rb") as f:
            msg = BytesParser(policy=policy.default).parse(f)
        emails = [*args.email, *EMAIL_RE.findall(str(msg.get("To", "")))]
        out = redact_message(msg, args.name, emails)
        dest = path.with_suffix(".redacted.eml")
        dest.write_bytes(out.as_bytes())
        print(f"{path} -> {dest}")


if __name__ == "__main__":
    main()
