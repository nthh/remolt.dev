"""
Remolt server — session manager + WebSocket-to-TTY bridge.

Single-file FastAPI server that:
1. Creates sandbox containers/pods on demand (POST /api/sessions)
2. Bridges browser WebSocket <-> container TTY (WS /ws/terminal/{id})
3. Cleans up idle sandboxes (background loop)

Auto-detects runtime: K8s (in-cluster) or Docker (local).
"""

from __future__ import annotations

import abc
import asyncio
import json as _json
import logging
import os
import ssl
import struct
import sys
import time
import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import AsyncIterator

import httpx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SANDBOX_IMAGE = os.getenv("REMOLT_SANDBOX_IMAGE", "remolt-sandbox")
STATIC_DIR = os.getenv("REMOLT_STATIC_DIR", "")
MAX_IDLE_SECONDS = int(os.getenv("REMOLT_MAX_IDLE_SECONDS", "3600"))
CLEANUP_INTERVAL = int(os.getenv("REMOLT_CLEANUP_INTERVAL", "60"))
MAX_SESSIONS = int(os.getenv("REMOLT_MAX_SESSIONS", "10"))
WARM_POOL_SIZE = int(os.getenv("REMOLT_WARM_POOL", "0"))
NAMESPACE = os.getenv("REMOLT_NAMESPACE", "remolt")

logger = logging.getLogger("remolt")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)

# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

EVENTS_LOG = os.getenv("REMOLT_EVENTS_LOG", "")
SESSIONS_FILE = os.getenv("REMOLT_SESSIONS_FILE", "")


def emit(event: str, **data) -> None:
    record = {"ts": time.time(), "event": event, **data}
    line = _json.dumps(record) + "\n"
    sys.stdout.write(line)
    sys.stdout.flush()
    if EVENTS_LOG:
        try:
            with open(EVENTS_LOG, "a") as f:
                f.write(line)
        except OSError:
            pass


def save_sessions() -> None:
    if not SESSIONS_FILE:
        return
    try:
        data = [
            {
                "session_id": s.session_id,
                "sandbox_id": s.sandbox_id,
                "created_at": s.created_at,
                "last_activity": s.last_activity,
                "has_repo": s.has_repo,
            }
            for s in sessions.values()
            if s.status == Status.RUNNING
        ]
        with open(SESSIONS_FILE, "w") as f:
            _json.dump(data, f)
    except OSError:
        pass


def load_sessions() -> list[dict]:
    if not SESSIONS_FILE:
        return []
    try:
        with open(SESSIONS_FILE) as f:
            return _json.load(f)
    except (OSError, _json.JSONDecodeError):
        return []


# ---------------------------------------------------------------------------
# Session model
# ---------------------------------------------------------------------------


class Status(str, Enum):
    CREATING = "creating"
    RUNNING = "running"
    TERMINATED = "terminated"


@dataclass
class Session:
    session_id: str
    sandbox_id: str
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    status: Status = Status.CREATING
    has_repo: bool = False


sessions: dict[str, Session] = {}
warm_pool: asyncio.Queue[str] = asyncio.Queue()  # sandbox_ids ready to claim

# ---------------------------------------------------------------------------
# Sandbox backend protocol
# ---------------------------------------------------------------------------


class ExecStream(abc.ABC):
    """Async context manager for an exec session with stdin/stdout + resize."""

    @abc.abstractmethod
    async def read(self) -> bytes | None:
        """Read next chunk from container stdout. None = EOF."""

    @abc.abstractmethod
    async def write(self, data: bytes) -> None:
        """Write to container stdin."""

    @abc.abstractmethod
    async def resize(self, cols: int, rows: int) -> None:
        """Resize the TTY."""

    @abc.abstractmethod
    async def close(self) -> None:
        """Clean up."""


class SandboxBackend(abc.ABC):
    """Abstract interface for sandbox lifecycle."""

    @abc.abstractmethod
    async def create(self, session_id: str, env: dict[str, str]) -> str:
        """Create and start a sandbox. Returns sandbox_id."""

    @abc.abstractmethod
    async def destroy(self, sandbox_id: str) -> None:
        """Stop and remove a sandbox."""

    @abc.abstractmethod
    async def list_managed(self) -> list[dict]:
        """List managed sandboxes. Returns [{id, session_id, running}]."""

    @abc.abstractmethod
    async def exec_attach(self, sandbox_id: str) -> ExecStream:
        """Attach to sandbox with TTY. Returns an ExecStream."""

    @abc.abstractmethod
    async def inject_env(self, sandbox_id: str, env: dict[str, str]) -> None:
        """Inject environment variables into a running sandbox and run setup."""

    @abc.abstractmethod
    async def close(self) -> None:
        """Shut down the backend."""


# ---------------------------------------------------------------------------
# Docker backend (local development)
# ---------------------------------------------------------------------------


class DockerExecStream(ExecStream):
    def __init__(self, stream, exec_id: str, docker):
        self._stream = stream
        self._exec_id = exec_id
        self._docker = docker

    async def read(self) -> bytes | None:
        msg = await self._stream.read_out()
        if msg is None:
            return None
        return msg.data if msg.data else None

    async def write(self, data: bytes) -> None:
        await self._stream.write_in(data)

    async def resize(self, cols: int, rows: int) -> None:
        async with self._docker._query(
            f"exec/{self._exec_id}/resize",
            method="POST",
            params={"h": rows, "w": cols},
        ):
            pass

    async def close(self) -> None:
        try:
            await self._stream.close()
        except Exception:
            pass


class DockerBackend(SandboxBackend):
    def __init__(self):
        import aiodocker
        self._docker = aiodocker.Docker()

    async def create(self, session_id: str, env: dict[str, str]) -> str:
        net_name = f"remolt-net-{session_id}"
        await self._docker.networks.create({"Name": net_name, "Driver": "bridge"})
        config = {
            "Image": SANDBOX_IMAGE,
            "Tty": True,
            "OpenStdin": True,
            "StdinOnce": False,
            "Env": [f"{k}={v}" for k, v in env.items()],
            "Labels": {
                "remolt.managed": "true",
                "remolt.session-id": session_id,
            },
            "HostConfig": {"NetworkMode": net_name},
        }
        container = await self._docker.containers.create_or_replace(
            name=f"remolt-{session_id}", config=config
        )
        await container.start()
        logger.info(f"Started container remolt-{session_id}")
        return container.id

    async def destroy(self, sandbox_id: str) -> None:
        try:
            c = self._docker.containers.container(sandbox_id)
            await c.stop(t=5)
        except Exception:
            pass
        try:
            c = self._docker.containers.container(sandbox_id)
            info = await c.show()
            net_name = info.get("HostConfig", {}).get("NetworkMode", "")
            await c.delete(force=True)
            if net_name.startswith("remolt-net-"):
                try:
                    await self._docker.networks.delete(net_name)
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"Failed to remove container {sandbox_id[:12]}: {e}")

    async def list_managed(self) -> list[dict]:
        containers = await self._docker.containers.list(
            all=True, filters={"label": ["remolt.managed=true"]}
        )
        result = []
        for c in containers:
            labels = c["Labels"] or {}
            result.append({
                "id": c.id,
                "session_id": labels.get("remolt.session-id", ""),
                "running": c["State"] == "running",
            })
        return result

    async def exec_attach(self, sandbox_id: str) -> ExecStream:
        container = self._docker.containers.container(sandbox_id)
        exec_inst = await container.exec(
            cmd=["bash", "-lc", "tmux new-session -As main"],
            stdin=True,
            stdout=True,
            stderr=True,
            tty=True,
            environment={"TERM": "xterm-256color", "COLUMNS": "120", "LINES": "30"},
        )
        exec_id = exec_inst._id
        stream = exec_inst.start(detach=False)
        await stream._init()
        return DockerExecStream(stream, exec_id, self._docker)

    async def inject_env(self, sandbox_id: str, env: dict[str, str]) -> None:
        container = self._docker.containers.container(sandbox_id)
        # Write env vars to a profile file, then run entrypoint setup
        lines = [f"export {k}={v}" for k, v in env.items()]
        script = " && ".join([
            f"echo '{chr(10).join(lines)}' > /home/dev/.remolt_env",
            "echo 'source /home/dev/.remolt_env 2>/dev/null' >> /home/dev/.bashrc",
            "source /home/dev/.remolt_env",
            # Re-run entrypoint logic (git config + clone)
            'git config --global user.name "${GIT_USER_NAME:-Claude Dev}"',
            'git config --global user.email "${GIT_USER_EMAIL:-dev@remolt.dev}"',
            'if [ -n "$REPO_URL" ]; then git clone "$REPO_URL" /home/dev/workspace 2>/dev/null || true; fi',
        ])
        exec_inst = await container.exec(
            cmd=["bash", "-c", script],
            environment=env,
        )
        await exec_inst.start(detach=True)

    async def close(self) -> None:
        await self._docker.close()


# ---------------------------------------------------------------------------
# K8s backend (in-cluster)
# ---------------------------------------------------------------------------

K8S_SA_PATH = Path("/var/run/secrets/kubernetes.io/serviceaccount")


def _in_cluster() -> bool:
    return K8S_SA_PATH.exists()


class K8sExecStream(ExecStream):
    """WebSocket-based exec stream using the K8s exec subprotocol.

    K8s exec multiplexes channels over WebSocket:
    - Channel 0: stdin
    - Channel 1: stdout
    - Channel 2: stderr
    - Channel 4: resize (JSON {"Width": cols, "Height": rows})
    Each binary frame is prefixed with a 1-byte channel number.
    """

    def __init__(self, ws):
        self._ws = ws
        self._buf = bytearray()

    async def read(self) -> bytes | None:
        while True:
            try:
                frame = await asyncio.wait_for(self._ws.recv(), timeout=30)
            except asyncio.TimeoutError:
                return b""
            except Exception:
                return None
            if isinstance(frame, bytes) and len(frame) > 1:
                channel = frame[0]
                data = frame[1:]
                if channel in (1, 2):  # stdout or stderr
                    return data
            elif isinstance(frame, str):
                # Shouldn't happen with binary subprotocol, skip
                continue

    async def write(self, data: bytes) -> None:
        # Channel 0 = stdin
        await self._ws.send(b"\x00" + data)

    async def resize(self, cols: int, rows: int) -> None:
        msg = _json.dumps({"Width": cols, "Height": rows}).encode()
        await self._ws.send(b"\x04" + msg)

    async def close(self) -> None:
        try:
            await self._ws.close()
        except Exception:
            pass


class K8sBackend(SandboxBackend):
    def __init__(self):
        token = (K8S_SA_PATH / "token").read_text().strip()
        ca_path = str(K8S_SA_PATH / "ca.crt")
        self._namespace = NAMESPACE
        self._headers = {"Authorization": f"Bearer {token}"}
        self._ssl_ctx = ssl.create_default_context(cafile=ca_path)
        self._base = "https://kubernetes.default.svc"
        self._client = httpx.AsyncClient(
            base_url=self._base,
            headers=self._headers,
            verify=self._ssl_ctx,
            timeout=30,
        )

    async def create(self, session_id: str, env: dict[str, str]) -> str:
        pod_name = f"remolt-{session_id[:16]}"
        pod_manifest = {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": pod_name,
                "namespace": self._namespace,
                "labels": {
                    "remolt.managed": "true",
                    "remolt.session-id": session_id,
                },
            },
            "spec": {
                "restartPolicy": "Never",
                "containers": [{
                    "name": "sandbox",
                    "image": SANDBOX_IMAGE,
                    "tty": True,
                    "stdin": True,
                    "env": [{"name": k, "value": v} for k, v in env.items()],
                    "resources": {
                        "requests": {"cpu": "250m", "memory": "512Mi"},
                        "limits": {"cpu": "2", "memory": "2Gi"},
                    },
                }],
            },
        }
        resp = await self._client.post(
            f"/api/v1/namespaces/{self._namespace}/pods",
            json=pod_manifest,
        )
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"K8s pod create failed: {resp.status_code} {resp.text}")
        logger.info(f"Created pod {pod_name}")

        # Wait for pod to be running (up to 120s)
        for _ in range(60):
            await asyncio.sleep(2)
            r = await self._client.get(
                f"/api/v1/namespaces/{self._namespace}/pods/{pod_name}"
            )
            if r.status_code == 200:
                phase = r.json().get("status", {}).get("phase", "")
                if phase == "Running":
                    logger.info(f"Pod {pod_name} is running")
                    return pod_name
                if phase in ("Failed", "Succeeded"):
                    raise RuntimeError(f"Pod {pod_name} entered {phase}")
        raise RuntimeError(f"Pod {pod_name} did not start in time")

    async def destroy(self, sandbox_id: str) -> None:
        try:
            resp = await self._client.delete(
                f"/api/v1/namespaces/{self._namespace}/pods/{sandbox_id}",
                params={"gracePeriodSeconds": "5"},
            )
            if resp.status_code in (200, 202, 404):
                logger.info(f"Deleted pod {sandbox_id}")
            else:
                logger.warning(f"Failed to delete pod {sandbox_id}: {resp.status_code}")
        except Exception as e:
            logger.warning(f"Failed to delete pod {sandbox_id}: {e}")

    async def list_managed(self) -> list[dict]:
        resp = await self._client.get(
            f"/api/v1/namespaces/{self._namespace}/pods",
            params={"labelSelector": "remolt.managed=true"},
        )
        if resp.status_code != 200:
            return []
        result = []
        for pod in resp.json().get("items", []):
            labels = pod.get("metadata", {}).get("labels", {})
            phase = pod.get("status", {}).get("phase", "")
            result.append({
                "id": pod["metadata"]["name"],
                "session_id": labels.get("remolt.session-id", ""),
                "running": phase == "Running",
            })
        return result

    async def exec_attach(self, sandbox_id: str) -> ExecStream:
        import websockets

        # Build exec URL
        params = (
            "command=bash&command=-lc&command=tmux+new-session+-As+main"
            "&stdin=true&stdout=true&stderr=true&tty=true"
        )
        url = (
            f"wss://kubernetes.default.svc"
            f"/api/v1/namespaces/{self._namespace}/pods/{sandbox_id}"
            f"/exec?container=sandbox&{params}"
        )
        token = (K8S_SA_PATH / "token").read_text().strip()
        ws = await websockets.connect(
            url,
            additional_headers={"Authorization": f"Bearer {token}"},
            ssl=self._ssl_ctx,
            subprotocols=["v4.channel.k8s.io"],
        )
        return K8sExecStream(ws)

    async def inject_env(self, sandbox_id: str, env: dict[str, str]) -> None:
        import websockets
        from urllib.parse import quote

        lines = "\n".join(f"export {k}={v}" for k, v in env.items())
        script = (
            f"echo '{lines}' > /home/dev/.remolt_env"
            " && echo 'source /home/dev/.remolt_env 2>/dev/null' >> /home/dev/.bashrc"
            " && source /home/dev/.remolt_env"
            ' && git config --global user.name "${GIT_USER_NAME:-Claude Dev}"'
            ' && git config --global user.email "${GIT_USER_EMAIL:-dev@remolt.dev}"'
            ' && if [ -n "$REPO_URL" ]; then git clone "$REPO_URL" /home/dev/workspace 2>/dev/null || true; fi'
        )
        params = f"command=bash&command=-c&command={quote(script)}&stderr=true&stdout=true"
        url = (
            f"wss://kubernetes.default.svc"
            f"/api/v1/namespaces/{self._namespace}/pods/{sandbox_id}"
            f"/exec?container=sandbox&{params}"
        )
        token = (K8S_SA_PATH / "token").read_text().strip()
        async with websockets.connect(
            url,
            additional_headers={"Authorization": f"Bearer {token}"},
            ssl=self._ssl_ctx,
            subprotocols=["v4.channel.k8s.io"],
        ) as ws:
            # Wait for exec to complete
            async for frame in ws:
                pass

    async def close(self) -> None:
        await self._client.aclose()


# ---------------------------------------------------------------------------
# Backend instance
# ---------------------------------------------------------------------------

backend: SandboxBackend | None = None


# ---------------------------------------------------------------------------
# Session recovery
# ---------------------------------------------------------------------------


async def recover_sessions() -> None:
    assert backend is not None
    saved = {s["session_id"]: s for s in load_sessions()}
    managed = await backend.list_managed()

    live_sids: set[str] = set()
    for sb in managed:
        sid = sb["session_id"]
        if sb["running"] and sid:
            meta = saved.get(sid, {})
            sessions[sid] = Session(
                session_id=sid,
                sandbox_id=sb["id"],
                created_at=meta.get("created_at", time.time()),
                last_activity=time.time(),
                status=Status.RUNNING,
                has_repo=meta.get("has_repo", False),
            )
            live_sids.add(sid)
            logger.info(f"Recovered session {sid}")
            emit("session.recovered", session_id=sid)
        else:
            await backend.destroy(sb["id"])
            logger.info(f"Cleaned up dead sandbox {sb['id']}")

    if live_sids:
        save_sessions()
        logger.info(f"Recovered {len(live_sids)} session(s)")


# ---------------------------------------------------------------------------
# Cleanup loop
# ---------------------------------------------------------------------------


async def cleanup_loop() -> None:
    while True:
        await asyncio.sleep(CLEANUP_INTERVAL)
        now = time.time()
        to_remove: list[str] = []
        for sid, s in sessions.items():
            if s.status != Status.RUNNING:
                continue
            idle = now - s.last_activity
            if idle > MAX_IDLE_SECONDS:
                logger.info(f"Session {sid} idle {idle:.0f}s — cleaning up")
                to_remove.append(sid)
        for sid in to_remove:
            s = sessions.pop(sid, None)
            if s:
                duration = time.time() - s.created_at
                await backend.destroy(s.sandbox_id)
                emit("session.ended", session_id=sid, reason="idle", duration_s=round(duration))
        if to_remove:
            save_sessions()


# ---------------------------------------------------------------------------
# Warm pool
# ---------------------------------------------------------------------------


async def warm_pool_loop() -> None:
    """Keep WARM_POOL_SIZE idle sandboxes ready for instant session creation."""
    if WARM_POOL_SIZE <= 0:
        return
    while True:
        deficit = WARM_POOL_SIZE - warm_pool.qsize()
        for _ in range(deficit):
            try:
                pool_id = secrets.token_urlsafe(8)
                sandbox_id = await backend.create(f"warm-{pool_id}", {"TERM": "xterm-256color"})
                warm_pool.put_nowait(sandbox_id)
                logger.info(f"Warm pool: created {sandbox_id} (pool size: {warm_pool.qsize()})")
                emit("warm_pool.created", sandbox_id=sandbox_id, pool_size=warm_pool.qsize())
            except Exception as e:
                logger.warning(f"Warm pool: failed to create sandbox: {e}")
                break
        await asyncio.sleep(5)


async def claim_warm_sandbox(env: dict[str, str]) -> str | None:
    """Try to claim a pre-warmed sandbox. Returns sandbox_id or None."""
    try:
        sandbox_id = warm_pool.get_nowait()
        await backend.inject_env(sandbox_id, env)
        logger.info(f"Claimed warm sandbox {sandbox_id} (pool size: {warm_pool.qsize()})")
        emit("warm_pool.claimed", sandbox_id=sandbox_id, pool_size=warm_pool.qsize())
        return sandbox_id
    except asyncio.QueueEmpty:
        return None


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    global backend
    if _in_cluster():
        backend = K8sBackend()
        logger.info("Using K8s backend (in-cluster)")
    else:
        backend = DockerBackend()
        logger.info("Using Docker backend (local)")

    await recover_sessions()
    cleanup_task = asyncio.create_task(cleanup_loop())
    warm_task = asyncio.create_task(warm_pool_loop())
    logger.info("Remolt server started")
    emit("server.started", max_sessions=MAX_SESSIONS, max_idle_s=MAX_IDLE_SECONDS,
         backend="k8s" if _in_cluster() else "docker", warm_pool=WARM_POOL_SIZE)
    yield
    cleanup_task.cancel()
    warm_task.cancel()
    # Drain warm pool
    while not warm_pool.empty():
        try:
            sandbox_id = warm_pool.get_nowait()
            await backend.destroy(sandbox_id)
        except Exception:
            pass
    for sid in list(sessions):
        s = sessions.pop(sid, None)
        if s:
            duration = time.time() - s.created_at
            await backend.destroy(s.sandbox_id)
            emit("session.ended", session_id=sid, reason="shutdown", duration_s=round(duration))
    await backend.close()
    emit("server.stopped")
    logger.info("Remolt server stopped")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="Remolt", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------


class CreateSessionReq(BaseModel):
    api_key: str | None = None
    repo_url: str | None = None
    git_user_name: str | None = None
    git_user_email: str | None = None


class SessionResp(BaseModel):
    session_id: str
    status: str
    ws_url: str


@app.get("/health")
async def health():
    return {"status": "ok", "sessions": len(sessions)}


@app.post("/api/sessions", response_model=SessionResp)
async def api_create_session(body: CreateSessionReq):
    if len(sessions) >= MAX_SESSIONS:
        raise HTTPException(429, "Max sessions reached")

    sid = secrets.token_urlsafe(32)
    env: dict[str, str] = {"TERM": "xterm-256color"}
    if body.api_key:
        env["ANTHROPIC_API_KEY"] = body.api_key
    if body.repo_url:
        env["REPO_URL"] = body.repo_url
    if body.git_user_name:
        env["GIT_USER_NAME"] = body.git_user_name
    if body.git_user_email:
        env["GIT_USER_EMAIL"] = body.git_user_email

    try:
        sandbox_id = await claim_warm_sandbox(env)
        if not sandbox_id:
            sandbox_id = await backend.create(sid, env)
    except Exception as e:
        raise HTTPException(500, f"Failed to create sandbox: {e}")

    sessions[sid] = Session(
        session_id=sid,
        sandbox_id=sandbox_id,
        status=Status.RUNNING,
        has_repo=bool(body.repo_url),
    )
    emit("session.created", session_id=sid, has_repo=bool(body.repo_url))
    save_sessions()
    return SessionResp(session_id=sid, status="running", ws_url=f"/ws/terminal/{sid}")


@app.get("/api/sessions/{session_id}", response_model=SessionResp)
async def api_get_session(session_id: str):
    s = sessions.get(session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    return SessionResp(
        session_id=s.session_id, status=s.status.value, ws_url=f"/ws/terminal/{s.session_id}"
    )


@app.delete("/api/sessions/{session_id}")
async def api_delete_session(session_id: str):
    s = sessions.pop(session_id, None)
    if not s:
        raise HTTPException(404, "Session not found")
    duration = time.time() - s.created_at
    await backend.destroy(s.sandbox_id)
    emit("session.ended", session_id=session_id, reason="user", duration_s=round(duration))
    save_sessions()
    return {"status": "terminated", "session_id": session_id}


# ---------------------------------------------------------------------------
# WebSocket terminal bridge
# ---------------------------------------------------------------------------


@app.websocket("/ws/terminal/{session_id}")
async def ws_terminal(ws: WebSocket, session_id: str):
    s = sessions.get(session_id)
    if not s or s.status != Status.RUNNING:
        await ws.close(code=4004, reason="Session not found")
        return

    await ws.accept()
    assert backend is not None

    try:
        stream = await backend.exec_attach(s.sandbox_id)
    except Exception as e:
        logger.error(f"Session {session_id}: exec_attach failed: {e}")
        await ws.close(code=4005, reason="Failed to attach to sandbox")
        return

    emit("terminal.connected", session_id=session_id)

    async def read_from_sandbox():
        try:
            while True:
                data = await stream.read()
                if data is None:
                    logger.info(f"Session {session_id}: stream ended")
                    break
                if data:
                    await ws.send_bytes(data)
                    s.last_activity = time.time()
        except (asyncio.CancelledError, WebSocketDisconnect):
            pass
        except Exception as e:
            logger.warning(f"Session {session_id}: sandbox read error: {type(e).__name__}: {e}")

    async def read_from_client():
        try:
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break

                if "bytes" in msg and msg["bytes"]:
                    await stream.write(msg["bytes"])
                    s.last_activity = time.time()

                elif "text" in msg and msg["text"]:
                    try:
                        ctrl = _json.loads(msg["text"])
                        if ctrl.get("type") == "resize":
                            cols = ctrl.get("cols", 120)
                            rows = ctrl.get("rows", 30)
                            await stream.resize(cols, rows)
                    except (_json.JSONDecodeError, KeyError, Exception):
                        pass
                    s.last_activity = time.time()

        except WebSocketDisconnect:
            logger.info(f"Session {session_id}: client disconnected")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"Session {session_id}: client read error: {type(e).__name__}: {e}")

    sandbox_task = asyncio.create_task(read_from_sandbox())
    client_task = asyncio.create_task(read_from_client())

    try:
        done, pending = await asyncio.wait(
            [sandbox_task, client_task], return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
    finally:
        await stream.close()
        try:
            await ws.close()
        except Exception:
            pass

    emit("terminal.disconnected", session_id=session_id)
    logger.info(f"Terminal session {session_id} disconnected")


# ---------------------------------------------------------------------------
# Static file serving (SPA)
# ---------------------------------------------------------------------------

_static = Path(STATIC_DIR) if STATIC_DIR else None

if _static and _static.is_dir():
    app.mount("/assets", StaticFiles(directory=_static / "assets"), name="assets")

    @app.get("/{path:path}")
    async def spa_fallback(path: str):
        file = _static / path
        if file.is_file():
            return FileResponse(file)
        return FileResponse(_static / "index.html")
