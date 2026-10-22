import asyncio
import sqlite3
from email.message import EmailMessage

import httpx

from pi_core.db import KeyValue
from pi_core.mail.base import NeedsLogin
from pi_core.mail.graph import GRAPH, GraphMailSource


class FakeAuth:
    def access_token(self):
        return "token"


def mime(msg_id: str) -> bytes:
    msg = EmailMessage()
    msg["From"] = "aviso@bancochile.cl"
    msg["Subject"] = f"Compra {msg_id}"
    msg["Message-ID"] = msg_id
    msg.set_content("Compra por $12.990")
    return msg.as_bytes()


def make_source(handler, state=None):
    state = state or KeyValue(sqlite3.connect(":memory:"))
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GraphMailSource(FakeAuth(), "Gastos", state, client), state


def test_delta_sync_follows_pages_and_resumes_from_delta_link():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        assert request.headers["Authorization"] == "Bearer token"
        if "/me/mailFolders?" in url:
            return httpx.Response(200, json={"value": [{"id": "F1"}]})
        if url.startswith(f"{GRAPH}/me/mailFolders/F1/messages/delta"):
            return httpx.Response(200, json={
                "value": [{"id": "m1", "receivedDateTime": "2026-10-22T17:35:00Z"}],
                "@odata.nextLink": "https://graph.microsoft.com/v1.0/page2",
            })
        if url.endswith("/page2"):
            return httpx.Response(200, json={
                "value": [{"id": "m2"}, {"id": "gone", "@removed": {"reason": "deleted"}}],
                "@odata.deltaLink": "https://graph.microsoft.com/v1.0/delta-next",
            })
        if url.endswith("/delta-next"):
            return httpx.Response(200, json={"value": [], "@odata.deltaLink": "https://graph.microsoft.com/v1.0/delta-next"})
        if url.endswith("/$value"):
            msg_id = url.split("/messages/")[1].split("/")[0]
            return httpx.Response(200, content=mime(f"<{msg_id}@bancochile.cl>"))
        raise AssertionError(f"unexpected request {url}")

    source, state = make_source(handler)
    first = asyncio.run(source.fetch_new())
    assert [m.message_id for m in first] == ["<m1@bancochile.cl>", "<m2@bancochile.cl>"]
    assert first[0].received_at.isoformat() == "2026-10-22T17:35:00+00:00"
    assert state.get("graph.delta_link.Gastos") is None  # not until commit()
    source.commit()
    assert state.get("graph.delta_link.Gastos").endswith("/delta-next")

    assert asyncio.run(source.fetch_new()) == []
    assert calls[-1].endswith("/delta-next")


def test_cursor_not_advanced_when_download_fails():
    def handler(request):
        url = str(request.url)
        if "/me/mailFolders?" in url:
            return httpx.Response(200, json={"value": [{"id": "F1"}]})
        if "/delta" in url:
            return httpx.Response(200, json={"value": [{"id": "m1"}], "@odata.deltaLink": "https://g/next"})
        return httpx.Response(503)

    source, state = make_source(handler)
    try:
        asyncio.run(source.fetch_new())
    except httpx.HTTPStatusError:
        pass
    assert state.get("graph.delta_link.Gastos") is None


def test_expired_token_raises_needs_login():
    source, _ = make_source(lambda request: httpx.Response(401))
    try:
        asyncio.run(source.fetch_new())
    except NeedsLogin:
        return
    raise AssertionError("expected NeedsLogin")
