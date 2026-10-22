"""Microsoft Graph mail source (Outlook / Microsoft 365), read-only.

Auth uses the OAuth device-code flow, which suits a headless Pi: the Pi prints a
code, you enter it at microsoft.com/devicelogin from any device, and the refresh
token is cached on disk. New mail is found with the folder's delta query, so each
poll only returns what changed since the last one.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import msal

from pi_core.db import KeyValue
from pi_core.mail.base import MailMessage, NeedsLogin, message_from_raw

log = logging.getLogger(__name__)

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = ["Mail.Read"]  # msal adds offline_access (refresh token) by itself


class GraphAuth:
    def __init__(self, client_id: str, authority: str, cache_path: Path) -> None:
        self.cache_path = cache_path
        self.cache = msal.SerializableTokenCache()
        if cache_path.exists():
            self.cache.deserialize(cache_path.read_text())
        self.app = msal.PublicClientApplication(client_id, authority=authority, token_cache=self.cache)

    def _save(self) -> None:
        if self.cache.has_state_changed:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.cache_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(self.cache.serialize())

    def login_device_code(self, show: Callable[[str], None] = print) -> str:
        """Interactive login. Blocks until the code is entered or expires. Returns the account name."""
        flow = self.app.initiate_device_flow(scopes=SCOPES)
        if "user_code" not in flow:
            raise RuntimeError(f"Could not start device login: {flow.get('error_description', flow)}")
        show(flow["message"])
        result = self.app.acquire_token_by_device_flow(flow)
        if "access_token" not in result:
            raise RuntimeError(f"Login failed: {result.get('error_description', result)}")
        self._save()
        return result.get("id_token_claims", {}).get("preferred_username", "?")

    def access_token(self) -> str:
        accounts = self.app.get_accounts()
        if not accounts:
            raise NeedsLogin("No Microsoft account logged in")
        result = self.app.acquire_token_silent(SCOPES, account=accounts[0])
        if not result or "access_token" not in result:
            raise NeedsLogin((result or {}).get("error_description", "Refresh token expired or revoked"))
        self._save()
        return result["access_token"]


class GraphMailSource:
    def __init__(
        self,
        auth: GraphAuth,
        folder: str,
        state: KeyValue,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.auth = auth
        self.folder = folder
        self.state = state
        self.client = client or httpx.AsyncClient(timeout=30)
        self._delta_key = f"graph.delta_link.{folder}"
        self._folder_id: str | None = None
        self._pending_delta_link: str | None = None

    async def _get(self, url: str, params: dict[str, str] | None = None) -> httpx.Response:
        token = await asyncio.to_thread(self.auth.access_token)
        resp = await self.client.get(
            url,
            params=params,
            headers={"Authorization": f"Bearer {token}", "Prefer": "odata.maxpagesize=50"},
        )
        if resp.status_code == 401:
            raise NeedsLogin("Graph rejected the access token")
        resp.raise_for_status()
        return resp

    async def _resolve_folder(self) -> str:
        """Find the folder by display name, at the top level or under the Inbox."""
        if self._folder_id:
            return self._folder_id
        params = {"$filter": f"displayName eq '{self.folder}'", "$select": "id"}
        for url in (f"{GRAPH}/me/mailFolders", f"{GRAPH}/me/mailFolders/inbox/childFolders"):
            found = (await self._get(url, params)).json()["value"]
            if found:
                self._folder_id = found[0]["id"]
                return self._folder_id
        raise ValueError(f"Outlook folder {self.folder!r} not found (top level or inside Inbox)")

    async def _delta_items(self) -> tuple[list[dict[str, Any]], str]:
        url = self.state.get(self._delta_key)
        params = None
        if url is None:
            folder_id = await self._resolve_folder()
            url = f"{GRAPH}/me/mailFolders/{folder_id}/messages/delta"
            params = {"$select": "id,receivedDateTime"}
        items: list[dict[str, Any]] = []
        while True:
            try:
                data = (await self._get(url, params)).json()
            except httpx.HTTPStatusError as e:
                # An old or invalid delta link means a full resync; dedup downstream absorbs repeats.
                if e.response.status_code in (400, 404, 410) and self.state.get(self._delta_key):
                    log.warning("Delta link rejected (%s); starting a full resync", e.response.status_code)
                    self.state.delete(self._delta_key)
                    return await self._delta_items()
                raise
            items.extend(item for item in data["value"] if "@removed" not in item)
            if "@odata.nextLink" in data:
                url, params = data["@odata.nextLink"], None
                continue
            return items, data["@odata.deltaLink"]

    async def fetch_new(self) -> list[MailMessage]:
        items, delta_link = await self._delta_items()
        messages = []
        for item in items:
            raw = (await self._get(f"{GRAPH}/me/messages/{item['id']}/$value")).content
            received = datetime.fromisoformat(item["receivedDateTime"]) if item.get("receivedDateTime") else None
            messages.append(message_from_raw(raw, received_at=received))
        self._pending_delta_link = delta_link
        return messages

    def commit(self) -> None:
        if self._pending_delta_link:
            self.state.set(self._delta_key, self._pending_delta_link)
            self._pending_delta_link = None
