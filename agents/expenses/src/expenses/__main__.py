"""Command line entry point.

    python -m expenses login       # one-time Microsoft device-code login (Graph source only)
    python -m expenses fetch-once  # poll the mailbox once and exit
    python -m expenses run         # poll forever (what the systemd service runs)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from pi_core.db import KeyValue
from pi_core.log import setup_logging
from pi_core.mail.base import MailSource, NeedsLogin
from pi_core.mail.graph import GraphAuth, GraphMailSource
from pi_core.mail.imap import ImapMailSource

from expenses.agent import poll_once, run_forever
from expenses.capture import Capture
from expenses.config import Config, load_config
from expenses.store import Store


def graph_auth(config: Config) -> GraphAuth:
    if config.mail.graph is None:
        sys.exit("config: mail.graph section is missing")
    return GraphAuth(config.mail.graph.client_id, config.mail.graph.authority, config.token_cache_path)


def build_source(config: Config, store: Store) -> MailSource:
    state = KeyValue(store.conn)
    if config.mail.source == "graph":
        return GraphMailSource(graph_auth(config), config.mail.graph.folder, state)
    imap = config.mail.imap
    if imap is None:
        sys.exit("config: mail.imap section is missing")
    password = os.environ.get(imap.password_env)
    if not password:
        sys.exit(f"Environment variable {imap.password_env} is not set")
    return ImapMailSource(imap.host, imap.user, password, imap.folder, state, imap.port)


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m expenses", description="Expense tracker agent")
    ap.add_argument("command", choices=["login", "fetch-once", "run"])
    ap.add_argument("--config", type=Path, help="config.yaml path (default: $EXPENSES_CONFIG or agents/expenses/config.yaml)")
    args = ap.parse_args()

    log = setup_logging("expenses")
    config = load_config(args.config)

    if args.command == "login":
        try:
            user = graph_auth(config).login_device_code()
        except RuntimeError as e:
            sys.exit(str(e))
        print(f"Logged in as {user}. Token cached at {config.token_cache_path}")
        return

    store = Store(config.db_path)
    source = build_source(config, store)
    capture = Capture(config.capture, config.samples_dir)
    if args.command == "fetch-once":
        try:
            new = asyncio.run(poll_once(source, store, capture))
        except NeedsLogin as e:
            sys.exit(f"Mail login needed ({e}). Run: python -m expenses login")
        print(f"{new} new message(s). Samples in {config.samples_dir}")
    else:
        log.info("Starting; mail source=%s, data dir=%s", config.mail.source, config.data_dir)
        asyncio.run(run_forever(source, store, capture, config.mail.poll_seconds))


if __name__ == "__main__":
    main()
