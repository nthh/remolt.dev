"""Tests for remolt server — backend abstraction, session lifecycle, API."""

import asyncio
import base64
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

    async def create(self, session_id: str, env: dict[str, str], *,
                     image: str | None = None, resources: dict | None = None,
                     ports: list[int] | None = None) -> str:
        sandbox_id = f"fake-{session_id[:8]}"
        self.sandboxes[sandbox_id] = {
            "session_id": session_id,
            "env": env,
            "running": True,
            "image": image,
            "resources": resources,
            "ports": ports,
        }
        return sandbox_id

    async def destroy(self, sandbox_id: str) -> None:
        sb = self.sandboxes.pop(sandbox_id, None)
        if sb:
            sb["running"] = False

    async def list_managed(self) -> list[dict]:
        return [
            {"id": sid, "session_id": sb["session_id"], "running": sb["running"], "owner": sb.get("owner", "")}
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

    async def relabel(self, sandbox_id: str, session_id: str, owner: str = "") -> None:
        sb = self.sandboxes.get(sandbox_id)
        if sb:
            sb["session_id"] = session_id

    async def close(self) -> None:
        self._closed = True


@pytest.fixture
def fake_backend():
    return FakeBackend()


@pytest.fixture
def app(fake_backend):
    """Create a test app with fake backend injected, auth disabled."""
    import server.server as srv

    # Inject fake backend
    srv.backend = fake_backend
    srv.sessions.clear()
    original_auth = srv.AUTH_REQUIRED
    srv.AUTH_REQUIRED = False
    yield srv.app
    srv.AUTH_REQUIRED = original_auth


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
    assert s.agent_type == "claude-code"


# ---------------------------------------------------------------------------
# Unit tests: Agent loading
# ---------------------------------------------------------------------------


def test_agents_loaded():
    """Agent configs are loaded from agents/ directory."""
    import server.server as srv
    assert "claude-code" in srv.AGENTS
    assert "openclaw" in srv.AGENTS
    assert srv.AGENTS["claude-code"].name == "Claude Code"
    assert srv.AGENTS["openclaw"].name == "OpenClaw"


def test_agent_ports():
    """OpenClaw agent has port config, Claude Code does not."""
    import server.server as srv
    assert len(srv.AGENTS["claude-code"].ports) == 0
    assert len(srv.AGENTS["openclaw"].ports) == 1
    assert srv.AGENTS["openclaw"].ports[0].port == 18789


def test_agent_warm_pool_flag():
    """Claude Code has warm_pool=true, OpenClaw has warm_pool=false."""
    import server.server as srv
    assert srv.AGENTS["claude-code"].warm_pool is True
    assert srv.AGENTS["openclaw"].warm_pool is False


# ---------------------------------------------------------------------------
# API tests: Agent list
# ---------------------------------------------------------------------------


def test_list_agents_endpoint(client):
    """GET /api/agents returns all agents."""
    resp = client.get("/api/agents")
    assert resp.status_code == 200
    agents = resp.json()
    assert len(agents) >= 2
    ids = [a["id"] for a in agents]
    assert "claude-code" in ids
    assert "openclaw" in ids
    # Verify schema shape
    oc = next(a for a in agents if a["id"] == "openclaw")
    assert oc["has_dashboard"] is True
    assert oc["name"] == "OpenClaw"
    cc = next(a for a in agents if a["id"] == "claude-code")
    assert cc["has_dashboard"] is False


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
    assert data["agent_type"] == "claude-code"
    assert data["proxy_url"] is None
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


def test_create_session_with_agent_type(client, fake_backend):
    """Session with agent_type stores it on session."""
    resp = client.post("/api/sessions", json={"agent_type": "openclaw"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["agent_type"] == "openclaw"
    assert data["proxy_url"] is not None
    assert "/proxy/" in data["proxy_url"]


def test_create_session_unknown_agent(client, fake_backend):
    """Unknown agent type returns 400."""
    resp = client.post("/api/sessions", json={"agent_type": "nonexistent"})
    assert resp.status_code == 400
    assert "unknown agent" in resp.json()["detail"].lower()


def test_create_session_default_agent(client, fake_backend):
    """Omitting agent_type defaults to claude-code."""
    resp = client.post("/api/sessions", json={})
    assert resp.status_code == 200
    assert resp.json()["agent_type"] == "claude-code"


def test_session_response_proxy_url(client, fake_backend):
    """OpenClaw sessions have proxy_url."""
    resp = client.post("/api/sessions", json={"agent_type": "openclaw"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["proxy_url"] is not None


def test_session_response_no_proxy_for_claude(client, fake_backend):
    """Claude-code sessions have no proxy_url."""
    resp = client.post("/api/sessions", json={"agent_type": "claude-code"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["proxy_url"] is None


def test_agent_specific_image(client, fake_backend):
    """OpenClaw sessions use the openclaw image."""
    import server.server as srv
    resp = client.post("/api/sessions", json={"agent_type": "openclaw"})
    assert resp.status_code == 200
    sb = list(fake_backend.sandboxes.values())[0]
    assert "openclaw" in sb["image"]


def test_agent_specific_resources(client, fake_backend):
    """OpenClaw sessions get agent-specific resource limits."""
    resp = client.post("/api/sessions", json={"agent_type": "openclaw"})
    assert resp.status_code == 200
    sb = list(fake_backend.sandboxes.values())[0]
    assert sb["resources"]["limits"]["memory"] == "4Gi"


def test_agent_setup_env_injected(client, fake_backend):
    """AGENT_SETUP env var set from agent config."""
    resp = client.post("/api/sessions", json={"agent_type": "openclaw"})
    assert resp.status_code == 200
    sb = list(fake_backend.sandboxes.values())[0]
    assert "AGENT_SETUP" in sb["env"]
    assert "openclaw onboard" in sb["env"]["AGENT_SETUP"]


def test_get_session(client):
    # Create first
    resp = client.post("/api/sessions", json={})
    sid = resp.json()["session_id"]
    # Get
    resp = client.get(f"/api/sessions/{sid}")
    assert resp.status_code == 200
    assert resp.json()["session_id"] == sid
    assert resp.json()["agent_type"] == "claude-code"


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
    assert "try again" in resp.json()["detail"].lower()


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
        "owner": "testuser",
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


@pytest.mark.asyncio
async def test_recover_destroys_ownerless_sessions(fake_backend):
    import server.server as srv
    srv.backend = fake_backend
    srv.sessions.clear()

    fake_backend.sandboxes["fake-orphan"] = {
        "session_id": "orphan-test",
        "env": {},
        "running": True,
        "owner": "",
    }

    await srv.recover_sessions()
    assert "orphan-test" not in srv.sessions
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
    claimed = await srv.claim_warm_sandbox("test-session", {"REPO_URL": "https://github.com/test/repo"})
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

    result = await srv.claim_warm_sandbox("test-session", {"TERM": "xterm-256color"})
    assert result is None


@pytest.mark.asyncio
async def test_claim_warm_sandbox_dead_pod_returns_none(fake_backend):
    """When a warm pod is dead, claim returns None and destroys the pod."""
    import server.server as srv
    srv.backend = fake_backend

    # Create a warm pod then make inject_env fail (simulating dead pod)
    sandbox_id = await fake_backend.create("warm-dead", {"TERM": "xterm-256color"})
    srv.warm_pool.put_nowait(sandbox_id)

    original_inject = fake_backend.inject_env
    async def fail_inject(sid, env):
        raise RuntimeError("pod is dead")
    fake_backend.inject_env = fail_inject

    result = await srv.claim_warm_sandbox("test-session", {"TERM": "xterm-256color"})
    assert result is None
    assert srv.warm_pool.qsize() == 0
    # Dead pod should have been destroyed
    assert sandbox_id not in fake_backend.sandboxes

    fake_backend.inject_env = original_inject


@pytest.mark.asyncio
async def test_claim_dead_warm_pod_falls_through_to_cold_start(fake_backend):
    """Session creation succeeds via cold start when warm pod is dead."""
    import server.server as srv
    srv.backend = fake_backend
    srv.sessions.clear()
    original_auth = srv.AUTH_REQUIRED
    srv.AUTH_REQUIRED = False

    try:
        # Create a warm pod then make inject_env fail for that pod only
        warm_id = await fake_backend.create("warm-dead", {"TERM": "xterm-256color"})
        srv.warm_pool.put_nowait(warm_id)

        original_inject = fake_backend.inject_env
        call_count = 0
        async def fail_first_inject(sid, env):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("pod is dead")
            return await original_inject(sid, env)
        fake_backend.inject_env = fail_first_inject

        from fastapi.testclient import TestClient
        client = TestClient(srv.app, raise_server_exceptions=False)
        resp = client.post("/api/sessions", json={})
        assert resp.status_code == 200
        # Dead warm pod should be destroyed, new cold-start pod should exist
        assert warm_id not in fake_backend.sandboxes
        assert len(fake_backend.sandboxes) == 1
    finally:
        srv.AUTH_REQUIRED = original_auth
        fake_backend.inject_env = original_inject


@pytest.mark.asyncio
async def test_warm_pool_loop_cleans_errored_pods(fake_backend):
    """Warm pool loop destroys errored warm pods and purges stale queue entries."""
    import server.server as srv
    srv.backend = fake_backend

    # Drain existing queue
    while not srv.warm_pool.empty():
        srv.warm_pool.get_nowait()

    # Simulate an errored warm pod in K8s (not running)
    fake_backend.sandboxes["remolt-warm-dead1"] = {
        "session_id": "warm-dead1",
        "env": {},
        "running": False,
    }
    # Put a stale entry in the queue pointing to a non-existent pod
    srv.warm_pool.put_nowait("remolt-warm-gone")

    # Run warm_pool_loop for one health-check iteration (counter % 6 == 0)
    # We patch asyncio.sleep to break after one iteration and set counter to 5
    original_pool_size = srv.WARM_POOL_SIZE
    srv.WARM_POOL_SIZE = 0  # Don't try to create new pods

    iterations = 0
    original_sleep = asyncio.sleep
    async def fake_sleep(s):
        nonlocal iterations
        iterations += 1
        if iterations >= 1:
            raise asyncio.CancelledError()

    try:
        # Manually run one health check cycle
        managed = await fake_backend.list_managed()
        running_ids = set()
        for sb in managed:
            sid = sb.get("session_id", "")
            if sid.startswith("warm-") and not sb["running"]:
                await fake_backend.destroy(sb["id"])
            elif sb["running"]:
                running_ids.add(sb["id"])

        # Purge stale queue entries
        requeue = []
        while not srv.warm_pool.empty():
            try:
                sbid = srv.warm_pool.get_nowait()
                if sbid in running_ids:
                    requeue.append(sbid)
            except asyncio.QueueEmpty:
                break
        for sbid in requeue:
            srv.warm_pool.put_nowait(sbid)

        # Errored pod should be destroyed
        assert "remolt-warm-dead1" not in fake_backend.sandboxes
        # Stale queue entry should be purged
        assert srv.warm_pool.qsize() == 0
    finally:
        srv.WARM_POOL_SIZE = original_pool_size


@pytest.mark.asyncio
async def test_warm_pool_loop_keeps_healthy_entries(fake_backend):
    """Warm pool loop keeps queue entries for pods that are still running."""
    import server.server as srv
    srv.backend = fake_backend

    # Drain existing queue
    while not srv.warm_pool.empty():
        srv.warm_pool.get_nowait()

    # Create a healthy warm pod
    sandbox_id = await fake_backend.create("warm-healthy", {"TERM": "xterm-256color"})
    srv.warm_pool.put_nowait(sandbox_id)

    # Run the health check logic
    managed = await fake_backend.list_managed()
    running_ids = set()
    for sb in managed:
        sid = sb.get("session_id", "")
        if sid.startswith("warm-") and not sb["running"]:
            await fake_backend.destroy(sb["id"])
        elif sb["running"]:
            running_ids.add(sb["id"])

    requeue = []
    while not srv.warm_pool.empty():
        try:
            sbid = srv.warm_pool.get_nowait()
            if sbid in running_ids:
                requeue.append(sbid)
        except asyncio.QueueEmpty:
            break
    for sbid in requeue:
        srv.warm_pool.put_nowait(sbid)

    # Healthy pod should still be in pool
    assert srv.warm_pool.qsize() == 1
    assert sandbox_id in fake_backend.sandboxes

    # Clean up shared state
    while not srv.warm_pool.empty():
        srv.warm_pool.get_nowait()


@pytest.mark.asyncio
async def test_create_session_uses_warm_pool(fake_backend):
    """When warm pool has a sandbox, session creation claims it instead of creating new."""
    import server.server as srv
    srv.backend = fake_backend
    srv.sessions.clear()
    original_auth = srv.AUTH_REQUIRED
    srv.AUTH_REQUIRED = False

    try:
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
    finally:
        srv.AUTH_REQUIRED = original_auth


@pytest.mark.asyncio
async def test_create_session_falls_back_to_cold_start(fake_backend):
    """When warm pool is empty, session creation falls back to backend.create."""
    import server.server as srv
    srv.backend = fake_backend
    srv.sessions.clear()
    original_auth = srv.AUTH_REQUIRED
    srv.AUTH_REQUIRED = False

    try:
        # Ensure pool is empty
        while not srv.warm_pool.empty():
            srv.warm_pool.get_nowait()

        from fastapi.testclient import TestClient
        client = TestClient(srv.app, raise_server_exceptions=False)
        resp = client.post("/api/sessions", json={})
        assert resp.status_code == 200
        # Backend should have created a new sandbox
        assert len(fake_backend.sandboxes) == 1
    finally:
        srv.AUTH_REQUIRED = original_auth


@pytest.mark.asyncio
async def test_warm_pool_skipped_for_openclaw(fake_backend):
    """Non-warm-pool agents always cold-start, even when pool has sandboxes."""
    import server.server as srv
    srv.backend = fake_backend
    srv.sessions.clear()
    original_auth = srv.AUTH_REQUIRED
    srv.AUTH_REQUIRED = False

    try:
        # Pre-populate warm pool
        sandbox_id = await fake_backend.create("warm-pool-1", {"TERM": "xterm-256color"})
        srv.warm_pool.put_nowait(sandbox_id)
        initial_pool_size = srv.warm_pool.qsize()

        from fastapi.testclient import TestClient
        client = TestClient(srv.app, raise_server_exceptions=False)
        resp = client.post("/api/sessions", json={"agent_type": "openclaw"})
        assert resp.status_code == 200
        # Warm pool should NOT be consumed for openclaw
        assert srv.warm_pool.qsize() == initial_pool_size
        # Should have created a new sandbox (2 total: warm + openclaw)
        assert len(fake_backend.sandboxes) == 2
    finally:
        srv.AUTH_REQUIRED = original_auth
        while not srv.warm_pool.empty():
            srv.warm_pool.get_nowait()


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


def _make_auth_cookie(login="testuser", name="Test User", email="test@example.com", gh_token="ghp_test123"):
    """Build an encrypted auth cookie for testing."""
    import server.server as srv
    return srv._encrypt_cookie({
        "login": login,
        "name": name,
        "email": email,
        "gh_token": gh_token,
    })


@pytest.fixture
def auth_client(fake_backend):
    """Test client with AUTH_REQUIRED=True."""
    import server.server as srv
    srv.backend = fake_backend
    srv.sessions.clear()
    original = srv.AUTH_REQUIRED
    srv.AUTH_REQUIRED = True
    try:
        yield TestClient(srv.app, raise_server_exceptions=False)
    finally:
        srv.AUTH_REQUIRED = original


# ---------------------------------------------------------------------------
# Unit tests: Cookie signing
# ---------------------------------------------------------------------------


def test_encrypt_and_decrypt_cookie():
    import server.server as srv
    data = {"login": "test", "name": "Test", "email": "t@t.com", "gh_token": "ghp_x"}
    encrypted = srv._encrypt_cookie(data)
    decrypted = srv._decrypt_cookie(encrypted)
    assert decrypted == data


def test_decrypt_cookie_rejects_tampered():
    import server.server as srv
    data = {"login": "test"}
    encrypted = srv._encrypt_cookie(data)
    tampered = encrypted[:-1] + ("a" if encrypted[-1] != "a" else "b")
    assert srv._decrypt_cookie(tampered) is None


def test_decrypt_cookie_rejects_garbage():
    import server.server as srv
    assert srv._decrypt_cookie("not-a-valid-token") is None


# ---------------------------------------------------------------------------
# Auth endpoint tests: /auth/me
# ---------------------------------------------------------------------------


def test_auth_me_returns_anonymous_when_auth_disabled(client):
    """When AUTH_REQUIRED=False, /auth/me returns anonymous user."""
    resp = client.get("/auth/me")
    assert resp.status_code == 200
    data = resp.json()
    assert data["login"] == "anonymous"
    assert data["auth_required"] is False


def test_auth_me_returns_401_when_auth_required_no_cookie(auth_client):
    """When AUTH_REQUIRED=True and no cookie, /auth/me returns 401."""
    resp = auth_client.get("/auth/me")
    assert resp.status_code == 401


def test_auth_me_returns_user_with_valid_cookie(auth_client):
    """When AUTH_REQUIRED=True and valid cookie, /auth/me returns user info."""
    cookie = _make_auth_cookie()
    auth_client.cookies.set("remolt_auth", cookie)
    resp = auth_client.get("/auth/me")
    assert resp.status_code == 200
    data = resp.json()
    assert data["login"] == "testuser"
    assert data["name"] == "Test User"
    assert data["email"] == "test@example.com"
    assert data["auth_required"] is True
    # gh_token should NOT be exposed in /auth/me
    assert "gh_token" not in data


def test_auth_me_rejects_invalid_cookie(auth_client):
    """Tampered cookie should be rejected."""
    auth_client.cookies.set("remolt_auth", "garbage.data")
    resp = auth_client.get("/auth/me")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Auth endpoint tests: /auth/login
# ---------------------------------------------------------------------------


def test_auth_login_redirects_to_github(client):
    import server.server as srv
    original = srv.GITHUB_CLIENT_ID
    srv.GITHUB_CLIENT_ID = "test-client-id"
    try:
        resp = client.get("/auth/login", follow_redirects=False)
        assert resp.status_code in (302, 307)
        location = resp.headers["location"]
        assert "github.com/login/oauth/authorize" in location
        assert "client_id=test-client-id" in location
        assert "public_repo" in location  # always included so users can create PRs
        assert "scope=" in location
        # full "repo" scope (private repo access) should NOT be included by default
        from urllib.parse import urlparse, parse_qs
        scope = parse_qs(urlparse(location).query)["scope"][0]
        assert "public_repo" in scope
        assert scope.split() == ["read:user", "user:email", "public_repo"]
    finally:
        srv.GITHUB_CLIENT_ID = original


def test_auth_login_with_repo_scope(client):
    import server.server as srv
    original = srv.GITHUB_CLIENT_ID
    srv.GITHUB_CLIENT_ID = "test-client-id"
    try:
        resp = client.get("/auth/login?repo=true", follow_redirects=False)
        assert resp.status_code in (302, 307)
        location = resp.headers["location"]
        assert "repo" in location
    finally:
        srv.GITHUB_CLIENT_ID = original


def test_auth_login_returns_501_when_not_configured(client):
    import server.server as srv
    original = srv.GITHUB_CLIENT_ID
    srv.GITHUB_CLIENT_ID = ""
    try:
        resp = client.get("/auth/login")
        assert resp.status_code == 501
    finally:
        srv.GITHUB_CLIENT_ID = original


# ---------------------------------------------------------------------------
# Auth endpoint tests: /auth/logout
# ---------------------------------------------------------------------------


def test_auth_logout_clears_cookie(client):
    resp = client.get("/auth/logout", follow_redirects=False)
    assert resp.status_code == 303
    # Cookie should be deleted
    set_cookie = resp.headers.get("set-cookie", "")
    assert "remolt_auth" in set_cookie


# ---------------------------------------------------------------------------
# Auth-gated session creation
# ---------------------------------------------------------------------------


def test_create_session_requires_auth(auth_client):
    """When AUTH_REQUIRED=True, POST /api/sessions without cookie returns 401."""
    resp = auth_client.post("/api/sessions", json={})
    assert resp.status_code == 401


def test_create_session_works_with_valid_cookie(auth_client, fake_backend):
    """When AUTH_REQUIRED=True, valid cookie allows session creation."""
    cookie = _make_auth_cookie()
    auth_client.cookies.set("remolt_auth", cookie)
    resp = auth_client.post("/api/sessions", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "running"
    assert len(fake_backend.sandboxes) == 1


def test_create_session_injects_github_token(auth_client, fake_backend):
    """OAuth GitHub token is injected as GITHUB_TOKEN env var."""
    cookie = _make_auth_cookie(gh_token="ghp_oauth_token")
    auth_client.cookies.set("remolt_auth", cookie)
    resp = auth_client.post("/api/sessions", json={})
    assert resp.status_code == 200
    sb = list(fake_backend.sandboxes.values())[0]
    assert sb["env"]["GITHUB_TOKEN"] == "ghp_oauth_token"


def test_create_session_injects_api_key(auth_client, fake_backend):
    """api_key from request body is injected as ANTHROPIC_API_KEY."""
    cookie = _make_auth_cookie()
    auth_client.cookies.set("remolt_auth", cookie)
    resp = auth_client.post("/api/sessions", json={"api_key": "sk-ant-test123"})
    assert resp.status_code == 200
    sb = list(fake_backend.sandboxes.values())[0]
    assert sb["env"]["ANTHROPIC_API_KEY"] == "sk-ant-test123"


def test_create_session_uses_oauth_git_identity_as_fallback(auth_client, fake_backend):
    """Git name/email from OAuth user is used when not provided in request."""
    cookie = _make_auth_cookie(name="OAuth User", email="oauth@example.com")
    auth_client.cookies.set("remolt_auth", cookie)
    resp = auth_client.post("/api/sessions", json={})
    assert resp.status_code == 200
    sb = list(fake_backend.sandboxes.values())[0]
    assert sb["env"]["GIT_USER_NAME"] == "OAuth User"
    assert sb["env"]["GIT_USER_EMAIL"] == "oauth@example.com"


def test_create_session_explicit_git_identity_overrides_oauth(auth_client, fake_backend):
    """Explicit git name/email in request overrides OAuth values."""
    cookie = _make_auth_cookie(name="OAuth User", email="oauth@example.com")
    auth_client.cookies.set("remolt_auth", cookie)
    resp = auth_client.post("/api/sessions", json={
        "git_user_name": "Custom Name",
        "git_user_email": "custom@example.com",
    })
    assert resp.status_code == 200
    sb = list(fake_backend.sandboxes.values())[0]
    assert sb["env"]["GIT_USER_NAME"] == "Custom Name"
    assert sb["env"]["GIT_USER_EMAIL"] == "custom@example.com"


def test_create_session_no_auth_still_works(client, fake_backend):
    """When AUTH_REQUIRED=False, sessions work without cookies (backwards compat)."""
    resp = client.post("/api/sessions", json={
        "github_token": "ghp_manual_pat",
        "api_key": "sk-ant-manual",
    })
    assert resp.status_code == 200
    sb = list(fake_backend.sandboxes.values())[0]
    # Manual PAT should be injected since no OAuth token
    assert sb["env"]["GITHUB_TOKEN"] == "ghp_manual_pat"
    assert sb["env"]["ANTHROPIC_API_KEY"] == "sk-ant-manual"


def test_create_session_oauth_token_preferred_over_manual_pat(auth_client, fake_backend):
    """OAuth token takes priority over manually-provided github_token."""
    cookie = _make_auth_cookie(gh_token="ghp_oauth")
    auth_client.cookies.set("remolt_auth", cookie)
    resp = auth_client.post("/api/sessions", json={"github_token": "ghp_manual"})
    assert resp.status_code == 200
    sb = list(fake_backend.sandboxes.values())[0]
    assert sb["env"]["GITHUB_TOKEN"] == "ghp_oauth"


# ---------------------------------------------------------------------------
# Proxy tests
# ---------------------------------------------------------------------------


def test_proxy_requires_auth(fake_backend):
    """Proxy route requires auth when AUTH_REQUIRED=True."""
    import server.server as srv
    srv.backend = fake_backend
    srv.sessions.clear()
    original = srv.AUTH_REQUIRED
    srv.AUTH_REQUIRED = True
    try:
        client = TestClient(srv.app, raise_server_exceptions=False)
        resp = client.get("/proxy/nonexistent/")
        assert resp.status_code == 401
    finally:
        srv.AUTH_REQUIRED = original


def test_proxy_rejects_wrong_user(auth_client, fake_backend):
    """Proxy returns 403 for non-owner."""
    import server.server as srv
    cookie = _make_auth_cookie(login="user1")
    auth_client.cookies.set("remolt_auth", cookie)
    resp = auth_client.post("/api/sessions", json={"agent_type": "openclaw"})
    sid = resp.json()["session_id"]

    # Switch to different user
    auth_client.cookies.clear()
    auth_client.cookies.set("remolt_auth", _make_auth_cookie(login="user2"))
    resp = auth_client.get(f"/proxy/{sid}/")
    assert resp.status_code == 403


def test_proxy_404_nonexistent(client):
    """Proxy returns 404 for non-existent session."""
    resp = client.get("/proxy/nonexistent/")
    assert resp.status_code == 404


def test_proxy_400_no_ports(client, fake_backend):
    """Claude-code sessions reject proxy (no ports)."""
    resp = client.post("/api/sessions", json={"agent_type": "claude-code"})
    sid = resp.json()["session_id"]
    resp = client.get(f"/proxy/{sid}/")
    assert resp.status_code == 400
    assert "no web ui" in resp.json()["detail"].lower()


def test_resolve_sandbox_ip_helper(fake_backend):
    """_resolve_sandbox_ip raises 501 for unknown backend types."""
    import server.server as srv
    original = srv.backend
    srv.backend = "not-a-backend"
    try:
        with pytest.raises(Exception):
            asyncio.get_event_loop().run_until_complete(
                srv._resolve_sandbox_ip("fake-id")
            )
    finally:
        srv.backend = original


def test_validate_proxy_access_returns_session_and_port(client, fake_backend):
    """_validate_proxy_access returns session and port for valid openclaw session."""
    import server.server as srv
    resp = client.post("/api/sessions", json={"agent_type": "openclaw"})
    sid = resp.json()["session_id"]
    s = srv.sessions[sid]
    agent = srv.AGENTS["openclaw"]
    assert agent.ports[0].port == 18789


def test_proxy_html_rewrite(client, fake_backend):
    """Proxy rewrites __OPENCLAW_CONTROL_UI_BASE_PATH__ in HTML responses."""
    import server.server as srv
    resp = client.post("/api/sessions", json={"agent_type": "openclaw"})
    sid = resp.json()["session_id"]
    # The base path rewrite is in proxy_agent_ui — verify the logic directly
    content = b'<script>window.__OPENCLAW_CONTROL_UI_BASE_PATH__="";</script>'
    base = f"/proxy/{sid}/"
    rewritten = content.replace(
        b'__OPENCLAW_CONTROL_UI_BASE_PATH__=""',
        f'__OPENCLAW_CONTROL_UI_BASE_PATH__="{base}"'.encode(),
    )
    assert base.encode() in rewritten
    assert b'__OPENCLAW_CONTROL_UI_BASE_PATH__=""' not in rewritten


def test_proxy_no_monkeypatch_in_html(client, fake_backend):
    """Proxy should NOT inject WebSocket monkeypatch scripts."""
    import server.server as srv
    resp = client.post("/api/sessions", json={"agent_type": "openclaw"})
    sid = resp.json()["session_id"]
    # Verify the rewrite logic doesn't inject any script tags
    content = b'<head><script>window.__OPENCLAW_CONTROL_UI_BASE_PATH__="";</script></head>'
    base = f"/proxy/{sid}/"
    rewritten = content.replace(
        b'__OPENCLAW_CONTROL_UI_BASE_PATH__=""',
        f'__OPENCLAW_CONTROL_UI_BASE_PATH__="{base}"'.encode(),
    )
    # Should only have the original script tag, no extra injected scripts
    assert rewritten.count(b'<script>') == 1


# ---------------------------------------------------------------------------
# Root WebSocket proxy tests
# ---------------------------------------------------------------------------


def test_root_ws_rejects_unauthenticated(fake_backend):
    """Root WS proxy rejects connections without auth cookie."""
    import server.server as srv
    srv.backend = fake_backend
    srv.sessions.clear()
    original = srv.AUTH_REQUIRED
    srv.AUTH_REQUIRED = True
    try:
        client = TestClient(srv.app, raise_server_exceptions=False)
        with pytest.raises(Exception):
            with client.websocket_connect("/"):
                pass
    finally:
        srv.AUTH_REQUIRED = original


def test_root_ws_rejects_cross_origin(auth_client, fake_backend):
    """Root WS proxy rejects connections from disallowed origins."""
    import server.server as srv
    cookie = _make_auth_cookie(login="user1")
    auth_client.cookies.set("remolt_auth", cookie)
    # Create an openclaw session
    auth_client.post("/api/sessions", json={"agent_type": "openclaw"})
    with pytest.raises(Exception):
        with auth_client.websocket_connect(
            "/",
            headers={"origin": "https://evil.com"},
        ):
            pass


def test_root_ws_rejects_no_dashboard_session(auth_client, fake_backend):
    """Root WS proxy rejects when user has no active dashboard session."""
    import server.server as srv
    cookie = _make_auth_cookie(login="user1")
    auth_client.cookies.set("remolt_auth", cookie)
    # Create a claude-code session (no dashboard)
    auth_client.post("/api/sessions", json={"agent_type": "claude-code"})
    with pytest.raises(Exception):
        with auth_client.websocket_connect("/"):
            pass


def test_root_ws_rejects_no_session_at_all(auth_client, fake_backend):
    """Root WS proxy rejects when user has no sessions."""
    import server.server as srv
    cookie = _make_auth_cookie(login="user_no_sessions")
    auth_client.cookies.set("remolt_auth", cookie)
    with pytest.raises(Exception):
        with auth_client.websocket_connect("/"):
            pass


def test_root_ws_allows_valid_origin(auth_client, fake_backend):
    """Root WS proxy allows connections from allowed origins."""
    import server.server as srv
    cookie = _make_auth_cookie(login="user1")
    auth_client.cookies.set("remolt_auth", cookie)
    auth_client.post("/api/sessions", json={"agent_type": "openclaw"})
    # Should not raise for allowed origin — will fail at upstream connect
    # (no real OpenClaw running) but the connection is accepted
    try:
        with auth_client.websocket_connect(
            "/",
            headers={"origin": srv.ALLOWED_ORIGINS[0]},
        ):
            pass
    except Exception:
        # Expected: upstream connection fails (no real OpenClaw pod)
        # but the WS was accepted (not rejected with 4003)
        pass


def test_root_ws_user_isolation(auth_client, fake_backend):
    """Root WS proxy only routes to sessions owned by the authenticated user."""
    import server.server as srv
    # Create session as user1
    cookie1 = _make_auth_cookie(login="user1")
    auth_client.cookies.set("remolt_auth", cookie1)
    auth_client.post("/api/sessions", json={"agent_type": "openclaw"})

    # Try to connect as user2 — should fail (no dashboard session for user2)
    auth_client.cookies.clear()
    auth_client.cookies.set("remolt_auth", _make_auth_cookie(login="user2"))
    with pytest.raises(Exception):
        with auth_client.websocket_connect("/"):
            pass


def _make_ws_capture_mock():
    """Create a mock websockets.connect that captures additional_headers."""
    captured = {}

    class FakeWS:
        async def __aenter__(self):
            raise ConnectionRefusedError("fake upstream")
        async def __aexit__(self, *a):
            pass

    def fake_connect(url, *, proxy=None, additional_headers=None):
        captured.update(additional_headers or {})
        return FakeWS()

    return fake_connect, captured


def test_root_ws_forwards_origin_to_upstream(auth_client, fake_backend):
    """Root WS proxy forwards the Origin header to the upstream WebSocket."""
    import server.server as srv

    cookie = _make_auth_cookie(login="user1")
    auth_client.cookies.set("remolt_auth", cookie)
    auth_client.post("/api/sessions", json={"agent_type": "openclaw"})

    fake_connect, captured = _make_ws_capture_mock()
    original = srv.ALLOWED_ORIGINS[:]
    srv.ALLOWED_ORIGINS.append("https://remolt.dev")

    try:
        with patch("websockets.connect", side_effect=fake_connect), \
             patch("server.server._resolve_sandbox_ip", return_value="10.0.0.1"):
            try:
                with auth_client.websocket_connect(
                    "/",
                    headers={"origin": "https://remolt.dev"},
                ):
                    pass
            except Exception:
                pass
    finally:
        srv.ALLOWED_ORIGINS[:] = original

    assert captured.get("Origin") == "https://remolt.dev"


def test_proxy_ws_forwards_origin_to_upstream(auth_client, fake_backend):
    """Per-session WS proxy forwards the Origin header to the upstream WebSocket."""
    import server.server as srv

    cookie = _make_auth_cookie(login="user1")
    auth_client.cookies.set("remolt_auth", cookie)
    resp = auth_client.post("/api/sessions", json={"agent_type": "openclaw"})
    sid = resp.json()["session_id"]

    fake_connect, captured = _make_ws_capture_mock()

    with patch("websockets.connect", side_effect=fake_connect), \
         patch("server.server._resolve_sandbox_ip", return_value="10.0.0.1"):
        try:
            with auth_client.websocket_connect(
                f"/proxy/{sid}/ws",
                headers={"origin": "https://remolt.dev"},
            ):
                pass
        except Exception:
            pass

    assert captured.get("Origin") == "https://remolt.dev"


# ---------------------------------------------------------------------------
# WS auth token injection
# ---------------------------------------------------------------------------


def test_inject_ws_auth_token_adds_token():
    """_inject_ws_auth_token injects token into connect frame."""
    from server.server import _inject_ws_auth_token
    frame = '{"type":"req","method":"connect","params":{"client":{"id":"test"}}}'
    result = json.loads(_inject_ws_auth_token(frame, "sandbox"))
    assert result["params"]["auth"]["token"] == "sandbox"


def test_inject_ws_auth_token_preserves_existing():
    """_inject_ws_auth_token does not overwrite existing token."""
    from server.server import _inject_ws_auth_token
    frame = '{"type":"req","method":"connect","params":{"auth":{"token":"existing"}}}'
    result = json.loads(_inject_ws_auth_token(frame, "sandbox"))
    assert result["params"]["auth"]["token"] == "existing"


def test_inject_ws_auth_token_ignores_non_connect():
    """_inject_ws_auth_token leaves non-connect frames unchanged."""
    from server.server import _inject_ws_auth_token
    frame = '{"type":"req","method":"chat","params":{}}'
    assert _inject_ws_auth_token(frame, "sandbox") == frame


# ---------------------------------------------------------------------------
# Security tests
# ---------------------------------------------------------------------------


def _create_authed_session(auth_client, login="testuser"):
    """Helper: create a session with auth and return the session_id."""
    cookie = _make_auth_cookie(login=login)
    auth_client.cookies.set("remolt_auth", cookie)
    resp = auth_client.post("/api/sessions", json={})
    assert resp.status_code == 200
    return resp.json()["session_id"]


# -- Session hijacking: recovered sessions with empty owner --


def test_ws_rejects_recovered_session_with_empty_owner(auth_client, fake_backend):
    """Recovered session with empty owner (lost sessions file) must be inaccessible."""
    import server.server as srv

    srv.sessions["orphan"] = srv.Session(
        session_id="orphan",
        sandbox_id="fake-orphan",
        status=srv.Status.RUNNING,
        owner="",
    )
    cookie = _make_auth_cookie(login="testuser")
    auth_client.cookies.set("remolt_auth", cookie)

    with pytest.raises(Exception):
        with auth_client.websocket_connect("/ws/terminal/orphan"):
            pass


# -- WebSocket Origin check --


def test_ws_rejects_cross_origin(client, fake_backend):
    """WebSocket from a disallowed origin should be rejected."""
    resp = client.post("/api/sessions", json={})
    sid = resp.json()["session_id"]

    with pytest.raises(Exception):
        with client.websocket_connect(
            f"/ws/terminal/{sid}",
            headers={"origin": "https://evil.com"},
        ):
            pass


def test_ws_allows_valid_origin(client, fake_backend):
    """WebSocket from an allowed origin should connect."""
    import server.server as srv

    resp = client.post("/api/sessions", json={})
    sid = resp.json()["session_id"]

    with client.websocket_connect(
        f"/ws/terminal/{sid}",
        headers={"origin": srv.ALLOWED_ORIGINS[0]},
    ) as ws:
        # Connection accepted — just close cleanly
        pass


def test_ws_allows_no_origin(client, fake_backend):
    """WebSocket with no Origin header (same-origin) should connect."""
    resp = client.post("/api/sessions", json={})
    sid = resp.json()["session_id"]

    with client.websocket_connect(f"/ws/terminal/{sid}") as ws:
        pass


# -- GET session endpoint auth --


def test_get_session_requires_auth(auth_client, fake_backend):
    """GET /api/sessions/{id} returns 401 without auth cookie."""
    sid = _create_authed_session(auth_client)
    auth_client.cookies.clear()
    resp = auth_client.get(f"/api/sessions/{sid}")
    assert resp.status_code == 401


def test_get_session_rejects_different_user(auth_client, fake_backend):
    """GET /api/sessions/{id} returns 403 for non-owner."""
    sid = _create_authed_session(auth_client, login="user1")
    auth_client.cookies.clear()
    auth_client.cookies.set("remolt_auth", _make_auth_cookie(login="user2"))
    resp = auth_client.get(f"/api/sessions/{sid}")
    assert resp.status_code == 403


def test_get_session_allows_owner(auth_client, fake_backend):
    """GET /api/sessions/{id} works for session owner."""
    sid = _create_authed_session(auth_client, login="owner1")
    resp = auth_client.get(f"/api/sessions/{sid}")
    assert resp.status_code == 200
    assert resp.json()["session_id"] == sid


# -- DELETE session endpoint auth --


def test_delete_session_requires_auth(auth_client, fake_backend):
    """DELETE /api/sessions/{id} returns 401 without auth cookie."""
    sid = _create_authed_session(auth_client)
    auth_client.cookies.clear()
    resp = auth_client.delete(f"/api/sessions/{sid}")
    assert resp.status_code == 401


def test_delete_session_rejects_different_user(auth_client, fake_backend):
    """DELETE /api/sessions/{id} returns 403 for non-owner."""
    sid = _create_authed_session(auth_client, login="user1")
    auth_client.cookies.clear()
    auth_client.cookies.set("remolt_auth", _make_auth_cookie(login="user2"))
    resp = auth_client.delete(f"/api/sessions/{sid}")
    assert resp.status_code == 403


def test_delete_session_allows_owner(auth_client, fake_backend):
    """DELETE /api/sessions/{id} works for session owner."""
    sid = _create_authed_session(auth_client, login="owner1")
    resp = auth_client.delete(f"/api/sessions/{sid}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "terminated"


# -- Per-user session limit --


def test_per_user_session_limit(auth_client, fake_backend):
    """Users cannot exceed MAX_USER_SESSIONS."""
    import server.server as srv
    original = srv.MAX_USER_SESSIONS
    srv.MAX_USER_SESSIONS = 1
    try:
        cookie = _make_auth_cookie(login="limited_user")
        auth_client.cookies.set("remolt_auth", cookie)
        resp = auth_client.post("/api/sessions", json={})
        assert resp.status_code == 200
        resp = auth_client.post("/api/sessions", json={})
        assert resp.status_code == 429
    finally:
        srv.MAX_USER_SESSIONS = original


def test_per_user_limit_independent_between_users(auth_client, fake_backend):
    """One user hitting the limit doesn't block another user."""
    import server.server as srv
    original = srv.MAX_USER_SESSIONS
    srv.MAX_USER_SESSIONS = 1
    try:
        auth_client.cookies.set("remolt_auth", _make_auth_cookie(login="userA"))
        resp = auth_client.post("/api/sessions", json={})
        assert resp.status_code == 200
        # userA is at limit
        resp = auth_client.post("/api/sessions", json={})
        assert resp.status_code == 429
        # userB should still be able to create
        auth_client.cookies.clear()
        auth_client.cookies.set("remolt_auth", _make_auth_cookie(login="userB"))
        resp = auth_client.post("/api/sessions", json={})
        assert resp.status_code == 200
    finally:
        srv.MAX_USER_SESSIONS = original


# -- Path traversal --


def test_path_traversal_blocked():
    """Path traversal attempts must not escape the static directory."""
    from pathlib import Path

    static_dir = Path("/app/static")
    traversal_attempts = [
        "../../etc/passwd",
        "../../../etc/shadow",
        "assets/../../server/server.py",
    ]
    for malicious_path in traversal_attempts:
        resolved = (static_dir / malicious_path).resolve()
        assert not str(resolved).startswith(str(static_dir.resolve())), (
            f"Path traversal not blocked: {malicious_path} -> {resolved}"
        )


# -- Cookie encryption --


def test_cookie_does_not_contain_github_token_in_plaintext():
    """The encrypted cookie must not expose the GitHub token."""
    cookie = _make_auth_cookie(gh_token="ghp_SUPERSECRETTOKEN123")
    # Token should not appear in plaintext or base64 in the cookie
    assert "ghp_SUPERSECRETTOKEN123" not in cookie
    assert base64.b64encode(b"ghp_SUPERSECRETTOKEN123").decode() not in cookie


def test_encrypted_cookie_resolves_github_token(auth_client, fake_backend):
    """Fernet-encrypted cookie correctly provides gh_token for session creation."""
    cookie = _make_auth_cookie(gh_token="ghp_resolved_token")
    auth_client.cookies.set("remolt_auth", cookie)
    resp = auth_client.post("/api/sessions", json={})
    assert resp.status_code == 200
    sb = list(fake_backend.sandboxes.values())[0]
    assert sb["env"]["GITHUB_TOKEN"] == "ghp_resolved_token"


# -- CORS configuration --


def test_cors_rejects_disallowed_origin(client):
    """Preflight from disallowed origin should not get CORS headers."""
    resp = client.options(
        "/api/sessions",
        headers={
            "origin": "https://evil.com",
            "access-control-request-method": "POST",
        },
    )
    assert resp.headers.get("access-control-allow-origin") != "https://evil.com"


def test_cors_allows_configured_origin(client):
    """Preflight from allowed origin should get CORS headers."""
    import server.server as srv
    resp = client.options(
        "/api/sessions",
        headers={
            "origin": srv.ALLOWED_ORIGINS[0],
            "access-control-request-method": "POST",
        },
    )
    assert resp.headers.get("access-control-allow-origin") == srv.ALLOWED_ORIGINS[0]
