"""Logging setup shared by all agents. Logs go to stderr, which systemd sends to journald."""

import logging
import os


def setup_logging(agent: str) -> logging.Logger:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format=f"%(asctime)s {agent} %(levelname)s %(name)s: %(message)s",
    )
    # httpx logs every request at INFO, which would include Graph URLs on each poll.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return logging.getLogger(agent)
