# Feature: url-validator-hardening
"""Entry-point integration tests for URL validator hardening.

Verifies that credentialed and private-host URLs are rejected at every entry
point (REST /api/archive, /api/cite and the MCP archive_and_cite handler)
without echoing the input URL or credentials, creating tasks, or issuing HTTP.
"""

import json

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from keeplink_mcp.api.app import create_app
from keeplink_mcp.db.models import ArchiveTask
from keeplink_mcp.mcp_server.server import _handle_archive_and_cite

BASE_URL = "http://127.0.0.1:9210"

# Obviously fake placeholders: only used to assert they never leak.
SECRET_USER = "fakeuser"
SECRET_PASS = "fakepass123"
CREDENTIAL_URL = f"https://{SECRET_USER}:{SECRET_PASS}@example.com/page"
PRIVATE_URL = "http://192.168.1.10/admin"

REJECTED = [
    pytest.param(CREDENTIAL_URL, id="credentials"),
    pytest.param(PRIVATE_URL, id="private-host"),
]


@pytest.fixture
async def client(session_factory):
    """httpx client bound to the FastAPI app (in-memory SQLite)."""
    app = create_app(session_factory)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _count_tasks(session_factory) -> int:
    async with session_factory() as session:
        result = await session.execute(select(func.count()).select_from(ArchiveTask))
        return result.scalar()


def _assert_no_leak(text: str, url: str) -> None:
    assert url not in text
    assert SECRET_USER not in text
    assert SECRET_PASS not in text


class TestRestEntryPoints:
    """**Validates: Requirements 5.2**"""

    @pytest.mark.parametrize("endpoint", ["/api/archive", "/api/cite"])
    @pytest.mark.parametrize("url", REJECTED)
    async def test_rejected_with_422_and_no_leak(
        self, client: AsyncClient, session_factory, endpoint: str, url: str
    ):
        before = await _count_tasks(session_factory)

        resp = await client.post(endpoint, json={"url": url})

        assert resp.status_code == 422
        _assert_no_leak(resp.text, url)
        assert await _count_tasks(session_factory) == before


class TestMcpEntryPoint:
    """**Validates: Requirements 5.3**"""

    @respx.mock(assert_all_called=False)
    async def test_credential_url_rejected_without_http(self, respx_mock):
        cite_route = respx_mock.post(f"{BASE_URL}/api/cite").mock(
            return_value=httpx.Response(200, json={})
        )

        result = await _handle_archive_and_cite({"url": CREDENTIAL_URL}, BASE_URL)

        assert len(result) == 1
        error = json.loads(result[0].text)["error"]
        assert error.startswith("Invalid URL format: ")
        _assert_no_leak(result[0].text, CREDENTIAL_URL)
        assert not cite_route.called
        assert len(respx_mock.calls) == 0
