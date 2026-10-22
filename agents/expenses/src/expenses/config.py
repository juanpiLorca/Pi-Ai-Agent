"""Agent configuration, loaded from YAML. Secrets (passwords, tokens) come from environment variables."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config.yaml"


class GraphConfig(BaseModel):
    client_id: str
    # "organizations" = any work/school account (e.g. @uc.cl); "consumers" = Outlook.com/Hotmail.
    authority: str = "https://login.microsoftonline.com/organizations"
    folder: str = "Gastos"


class ImapConfig(BaseModel):
    host: str
    port: int = 993
    user: str
    folder: str = "Gastos"
    password_env: str = "IMAP_PASSWORD"


class MailConfig(BaseModel):
    source: Literal["graph", "imap"] = "graph"
    graph: GraphConfig | None = None
    imap: ImapConfig | None = None
    poll_seconds: int = Field(60, ge=15)


class CaptureConfig(BaseModel):
    """Save redacted copies of bank/merchant emails to build parser fixtures."""

    enabled: bool = True
    sender_domains: list[str] = ["bancochile.cl", "bice.cl", "rappi.com", "rappi.cl", "copec.cl"]
    redact_names: list[str] = []
    redact_emails: list[str] = []


class Config(BaseModel):
    # systemd's StateDirectory= sets $STATE_DIRECTORY (e.g. /var/lib/pi-agents/expenses).
    data_dir: Path = Field(default_factory=lambda: Path(os.environ.get("STATE_DIRECTORY", "data")))
    payday: int = Field(28, ge=1, le=31)
    mail: MailConfig
    capture: CaptureConfig = CaptureConfig()

    @property
    def db_path(self) -> Path:
        return self.data_dir / "expenses.db"

    @property
    def samples_dir(self) -> Path:
        return self.data_dir / "samples"

    @property
    def token_cache_path(self) -> Path:
        return self.data_dir / "msal_token_cache.json"


def load_config(path: Path | None = None) -> Config:
    path = path or Path(os.environ.get("EXPENSES_CONFIG", DEFAULT_CONFIG))
    return Config.model_validate(yaml.safe_load(path.read_text()))
