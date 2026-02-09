"""Tests for remolt server — backend abstraction, session lifecycle, API."""

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class FakeExecStream:
    """Fake ExecStream for testing the WS bridge."""

    def __init__(self):
        self._inbox: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._written: list[bytes] = []
        self._resizes: list[tuple[int, int]] = []
        self._closed = False

    async def read(self) -> bytes | None:
        return await self._inbox.get()

    async def write(self, data: bytes) -> None:
        self._written.append(data)

    async def resize(self, cols: int, rows: int) -> None:
        self._resizes.append((cols, rows))

    async def close(self) -> None:
        self._closed = True

    def push(self, data: bytes) -> None:
        """Push data to be read by consumer."""
        self._inbox.put_nowait(data)

    def end(self) -> None:
        """Signal EOF."""
        self._inbox.put_nowait(None)


class FakeBackend:
    """In-memory sandbox backend for testing."""

    def __init__(self):
        self.sandboxes: dict[str, dict] = {}
        self.streams: dict[str, FakeExecStream] = {}
        self._closed = False

    async def create(self, session_id: str, env: dict[str, str]) -> str:
        sandbox_id = f"fake-{session_id[:8]}"
        self.sandboxes[sandbox_id] = {
            "session_id": session_id,
            "env": env,
            "running": True,
        }
        return sandbox_id

    async def destroy(self, sandbox_id: str) -> None:
        sb = self.sandboxes.pop(sandbox_id, None)
        if sb:
            sb["running"] = False

    async def list_managed(self) -> list[dict]:
        return [
            {"id": sid, "session_id": sb["session_id"], "running": sb["running"]}
            for sid, sb in self.sandboxes.items()
        ]

    async def exec_attach(self, sandbox_id: str) -> FakeExecStream:
        stream = FakeExecStream()
        self.streams[sandbox_id] = stream
        return stream

    async def inject_env(self, sandbox_id: str, env: dict[str, str]) -> None:
        sb = self.sandboxes.get(sandbox_id)
        if sb:
            sb["env"].update(env)
            sb["env_injected"] = True

    async def close(self) -> None:
        self._closed = True


@pytest.fixture
def fake_backend():
    return FakeBackend()


@pytest.fixture
def app(fake_backend):
    """Create a test app with fake backend injected."""
    import server.server as srv

    # Inject fake backend
    srv.backend = fake_backend
    srv.sessions.clear()
    return srv.app


@pytest.fixture
def client(app):
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Unit tests: Backend detection
# ---------------------------------------------------------------------------


def test_in_cluster_false(tmp_path):
    """_in_cluster returns False when SA path doesn't exist."""
    import server.server as srv
    with patch.object(srv, "K8S_SA_PATH", tmp_path / "nonexistent"):
        assert srv._in_cluster() is False


def test_in_cluster_true(tmp_path):
    """_in_cluster returns True when SA path exists."""
    import server.server as srv
    sa_path = tmp_path / "serviceaccount"
    sa_path.mkdir()
    with patch.object(srv, "K8S_SA_PATH", sa_path):
        assert srv._in_cluster() is True


# ---------------------------------------------------------------------------
# Unit tests: Session model
# ---------------------------------------------------------------------------


def test_session_defaults():
    from server.server import Session, Status
    s = Session(session_id="abc", sandbox_id="xyz")
    assert s.status == Status.CREATING
    assert s.has_repo is False
    assert s.last_activity > 0


# ---------------------------------------------------------------------------
# API tests: Health
# ---------------------------------------------------------------------------


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["sessions"] == 0


# ---------------------------------------------------------------------------
# API tests: Session CRUD
# ---------------------------------------------------------------------------


def test_create_session(client, fake_backend):
    resp = client.post("/api/sessions", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "running"
    assert data["session_id"]
    assert data["ws_url"].startswith("/ws/terminal/")
    # Backend should have a sandbox
    assert len(fake_backend.sandboxes) == 1


def test_create_session_with_repo(client, fake_backend):
    resp = client.post("/api/sessions", json={
        "repo_url": "https://github.com/test/repo",
        "git_user_name": "Test",
    })
    assert resp.status_code == 200
    sb = list(fake_backend.sandboxes.values())[0]
    assert sb["env"]["REPO_URL"] == "https://github.com/test/repo"
    assert sb["env"]["GIT_USER_NAME"] == "Test"


def test_get_session(client):
    # Create first
    resp = client.post("/api/sessions", json={})
    sid = resp.json()["session_id"]
    # Get
    resp = client.get(f"/api/sessions/{sid}")
    assert resp.status_code == 200
    assert resp.json()["session_id"] == sid


def test_get_session_not_found(client):
    resp = client.get("/api/sessions/nonexistent")
    assert resp.status_code == 404


def test_delete_session(client, fake_backend):
    # Create
    resp = client.post("/api/sessions", json={})
    sid = resp.json()["session_id"]
    assert len(fake_backend.sandboxes) == 1
    # Delete
    resp = client.delete(f"/api/sessions/{sid}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "terminated"
    # Backend sandbox should be removed
    assert len(fake_backend.sandboxes) == 0


def test_delete_session_not_found(client):
    resp = client.delete("/api/sessions/nonexistent")
    assert resp.status_code == 404


def test_max_sessions(client, fake_backend):
    import server.server as srv
    original = srv.MAX_SESSIONS
    srv.MAX_SESSIONS = 2
    try:
        client.post("/api/sessions", json={})
        client.post("/api/sessions", json={})
        resp = client.post("/api/sessions", json={})
        assert resp.status_code == 429
    finally:
        srv.MAX_SESSIONS = original


# ---------------------------------------------------------------------------
# API tests: Create session with failed backend
# ---------------------------------------------------------------------------


def test_create_session_backend_failure(client, fake_backend):
    async def fail(*a, **kw):
        raise RuntimeError("boom")
    fake_backend.create = fail
    resp = client.post("/api/sessions", json={})
    assert resp.status_code == 500
    assert "boom" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Unit tests: FakeExecStream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exec_stream():
    stream = FakeExecStream()
    stream.push(b"hello")
    data = await stream.read()
    assert data == b"hello"

    await stream.write(b"input")
    assert stream._written == [b"input"]

    await stream.resize(80, 24)
    assert stream._resizes == [(80, 24)]

    stream.end()
    data = await stream.read()
    assert data is None

    await stream.close()
    assert stream._closed


# ---------------------------------------------------------------------------
# Unit tests: Cleanup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_removes_idle_sessions(fake_backend):
    import server.server as srv
    srv.backend = fake_backend
    srv.sessions.clear()

    # Create a session via backend
    sandbox_id = await fake_backend.create("idle-test", {})
    srv.sessions["idle-test"] = srv.Session(
        session_id="idle-test",
        sandbox_id=sandbox_id,
        status=srv.Status.RUNNING,
        last_activity=time.time() - 9999,  # very idle
    )

    original = srv.MAX_IDLE_SECONDS
    srv.MAX_IDLE_SECONDS = 1
    try:
        # Run one cleanup cycle manually
        now = time.time()
        to_remove = []
        for sid, s in srv.sessions.items():
            if s.status == srv.Status.RUNNING and (now - s.last_activity) > srv.MAX_IDLE_SECONDS:
                to_remove.append(sid)
        for sid in to_remove:
            s = srv.sessions.pop(sid)
            await fake_backend.destroy(s.sandbox_id)

        assert len(srv.sessions) == 0
        assert len(fake_backend.sandboxes) == 0
    finally:
        srv.MAX_IDLE_SECONDS = original


# ---------------------------------------------------------------------------
# Unit tests: Recovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recover_sessions(fake_backend):
    import server.server as srv
    srv.backend = fake_backend
    srv.sessions.clear()

    # Pre-populate backend with a "running" sandbox
    fake_backend.sandboxes["fake-recovery"] = {
        "session_id": "recovery-test",
        "env": {},
        "running": True,
    }

    await srv.recover_sessions()
    assert "recovery-test" in srv.sessions
    assert srv.sessions["recovery-test"].status == srv.Status.RUNNING


@pytest.mark.asyncio
async def test_recover_cleans_dead_sandboxes(fake_backend):
    import server.server as srv
    srv.backend = fake_backend
    srv.sessions.clear()

    fake_backend.sandboxes["fake-dead"] = {
        "session_id": "dead-test",
        "env": {},
        "running": False,
    }

    await srv.recover_sessions()
    assert "dead-test" not in srv.sessions
    assert len(fake_backend.sandboxes) == 0


# ---------------------------------------------------------------------------
# Unit tests: Analytics
# ---------------------------------------------------------------------------


def test_emit(capsys):
    from server.server import emit
    emit("test.event", foo="bar")
    output = capsys.readouterr().out
    record = json.loads(output.strip())
    assert record["event"] == "test.event"
    assert record["foo"] == "bar"
    assert "ts" in record


# ---------------------------------------------------------------------------
# Unit tests: Warm pool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_warm_sandbox(fake_backend):
    import server.server as srv
    srv.backend = fake_backend

    # Pre-populate warm pool
    sandbox_id = await fake_backend.create("warm-test", {"TERM": "xterm-256color"})
    srv.warm_pool.put_nowait(sandbox_id)
    assert srv.warm_pool.qsize() == 1

    # Claim it
    claimed = await srv.claim_warm_sandbox({"REPO_URL": "https://github.com/test/repo"})
    assert claimed == sandbox_id
    assert srv.warm_pool.qsize() == 0
    # Env should have been injected
    sb = fake_backend.sandboxes[sandbox_id]
    assert sb["env"]["REPO_URL"] == "https://github.com/test/repo"
    assert sb["env_injected"] is True


@pytest.mark.asyncio
async def test_claim_warm_sandbox_empty(fake_backend):
    import server.server as srv
    srv.backend = fake_backend

    # Empty pool
    while not srv.warm_pool.empty():
        srv.warm_pool.get_nowait()

    result = await srv.claim_warm_sandbox({"TERM": "xterm-256color"})
    assert result is None


@pytest.mark.asyncio
async def test_create_session_uses_warm_pool(fake_backend):
    """When warm pool has a sandbox, session creation claims it instead of creating new."""
    import server.server as srv
    srv.backend = fake_backend
    srv.sessions.clear()

    # Pre-populate warm pool
    sandbox_id = await fake_backend.create("warm-pool-1", {"TERM": "xterm-256color"})
    srv.warm_pool.put_nowait(sandbox_id)

    initial_count = len(fake_backend.sandboxes)

    # Create session via API
    from fastapi.testclient import TestClient
    client = TestClient(srv.app, raise_server_exceptions=False)
    resp = client.post("/api/sessions", json={})
    assert resp.status_code == 200

    # Should have claimed from pool, not created a new one
    assert len(fake_backend.sandboxes) == initial_count
    assert srv.warm_pool.qsize() == 0


@pytest.mark.asyncio
async def test_create_session_falls_back_to_cold_start(fake_backend):
    """When warm pool is empty, session creation falls back to backend.create."""
    import server.server as srv
    srv.backend = fake_backend
    srv.sessions.clear()

    # Ensure pool is empty
    while not srv.warm_pool.empty():
        srv.warm_pool.get_nowait()

    from fastapi.testclient import TestClient
    client = TestClient(srv.app, raise_server_exceptions=False)
    resp = client.post("/api/sessions", json={})
    assert resp.status_code == 200
    # Backend should have created a new sandbox
    assert len(fake_backend.sandboxes) == 1
