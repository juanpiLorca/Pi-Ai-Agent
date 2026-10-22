"""Redact personal data from .eml files before sharing them as parser fixtures.

Usage (no install needed):
    python tools/redact_eml.py mail1.eml mail2.eml --name "Juan Lorca" --email juan.lorca@uc.cl

Writes <file>.redacted.eml next to each input. Review the output before sharing.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "pi_core" / "src"))

from pi_core.mail.redact import redact_raw  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--name", action="append", default=[], help="name to remove (repeatable)")
    ap.add_argument("--email", action="append", default=[], help="email address to remove (repeatable)")
    args = ap.parse_args()

    for path in args.files:
        dest = path.with_suffix(".redacted.eml")
        dest.write_bytes(redact_raw(path.read_bytes(), args.name, args.email))
        print(f"{path} -> {dest}")


if __name__ == "__main__":
    main()
