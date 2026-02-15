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
import shlex
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import AsyncIterator

import base64
import hashlib
import hmac

import httpx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Load .env file if present (local dev); no-op in K8s where env is injected
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

VERSION = os.getenv("COMMIT_SHA", "dev")

SANDBOX_IMAGE = os.getenv("REMOLT_SANDBOX_IMAGE", "remolt-sandbox")
STATIC_DIR = os.getenv("REMOLT_STATIC_DIR", "")
MAX_IDLE_SECONDS = int(os.getenv("REMOLT_MAX_IDLE_SECONDS", "3600"))
CLEANUP_INTERVAL = int(os.getenv("REMOLT_CLEANUP_INTERVAL", "60"))
MAX_SESSIONS = int(os.getenv("REMOLT_MAX_SESSIONS", "10"))
MAX_USER_SESSIONS = int(os.getenv("REMOLT_MAX_USER_SESSIONS", "2"))
WARM_POOL_SIZE = int(os.getenv("REMOLT_WARM_POOL", "0"))
SANDBOX_BANDWIDTH = os.getenv("REMOLT_SANDBOX_BANDWIDTH", "100mbit")  # per-pod tc rate limit
NAMESPACE = os.getenv("REMOLT_NAMESPACE", "remolt")

# GitHub OAuth
GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET", "")
COOKIE_SECRET = os.getenv("COOKIE_SECRET", secrets.token_hex(32))
AUTH_REQUIRED = bool(GITHUB_CLIENT_ID)
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("REMOLT_ALLOWED_ORIGINS", "http://localhost:5173").split(",") if o.strip()]

logger = logging.getLogger("remolt")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)

# ---------------------------------------------------------------------------
# Agent plugin system
# ---------------------------------------------------------------------------


@dataclass
class AgentPort:
    port: int
    label: str
    health_check: str = "/"


@dataclass
class AgentConfig:
    id: str
    name: str
    description: str
    install: str = ""
    setup: str = ""
    ports: list[AgentPort] = field(default_factory=list)
    resources: dict = field(default_factory=lambda: {
        "requests": {"cpu": "250m", "memory": "512Mi"},
        "limits": {"cpu": "2", "memory": "2Gi"},
    })
    env_defaults: dict[str, str] = field(default_factory=dict)
    env_schema: list[dict] = field(default_factory=list)
    welcome: str = ""
    warm_pool: bool = False
    icon: str = ""


def _load_agents() -> dict[str, AgentConfig]:
    """Discover and load agent configs from agents/*/agent.json."""
    agents: dict[str, AgentConfig] = {}
    # Search relative to project root (this file is in server/)
    search_paths = [
        Path(__file__).parent.parent / "agents",  # dev: project root
        Path("/app/agents"),  # production: baked into Docker image
    ]
    for agents_dir in search_paths:
        if not agents_dir.is_dir():
            continue
        for agent_json in sorted(agents_dir.glob("*/agent.json")):
            try:
                data = _json.loads(agent_json.read_text())
                ports = [AgentPort(**p) for p in data.get("ports", [])]
                agents[data["id"]] = AgentConfig(
                    id=data["id"],
                    name=data.get("name", data["id"]),
                    description=data.get("description", ""),
                    install=data.get("install", ""),
                    setup=data.get("setup", ""),
                    ports=ports,
                    resources=data.get("resources", {}),
                    env_defaults=data.get("env_defaults", {}),
                    env_schema=data.get("env_schema", []),
                    welcome=data.get("welcome", ""),
                    warm_pool=data.get("warm_pool", False),
                    icon=data.get("icon", ""),
                )
                logger.info(f"Loaded agent: {data['id']} ({agent_json})")
            except Exception as e:
                logger.warning(f"Failed to load agent from {agent_json}: {e}")
        if agents:
            break  # Use first directory that has agents
    return agents


def _agent_image(agent_id: str) -> str:
    """Resolve container image for an agent. Convention: ghcr.io/nthh/remolt-{id}:latest."""
    # Allow override via env var: REMOLT_CLAUDE_CODE_IMAGE, REMOLT_OPENCLAW_IMAGE, etc.
    env_key = f"REMOLT_{agent_id.upper().replace('-', '_')}_IMAGE"
    override = os.getenv(env_key)
    if override:
        return override
    # For claude-code, use SANDBOX_IMAGE for backwards compat
    if agent_id == "claude-code":
        return SANDBOX_IMAGE
    return f"ghcr.io/nthh/remolt-{agent_id}:latest"


AGENTS: dict[str, AgentConfig] = _load_agents()

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
                "owner": s.owner,
                "agent_type": s.agent_type,
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
    owner: str = ""
    agent_type: str = "claude-code"


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
    async def create(self, session_id: str, env: dict[str, str], *,
                     image: str | None = None, resources: dict | None = None,
                     ports: list[int] | None = None) -> str:
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
    async def relabel(self, sandbox_id: str, session_id: str, owner: str = "") -> None:
        """Update the session-id and owner labels on a running sandbox."""

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

    async def create(self, session_id: str, env: dict[str, str], *,
                     image: str | None = None, resources: dict | None = None,
                     ports: list[int] | None = None) -> str:
        net_name = f"remolt-net-{session_id}"
        await self._docker.networks.create({"Name": net_name, "Driver": "bridge"})
        config = {
            "Image": image or SANDBOX_IMAGE,
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
        # Separate secrets from non-secret env for setup
        secret_keys = {"GITHUB_TOKEN", "ANTHROPIC_API_KEY"}
        safe_lines = [f"export {k}={shlex.quote(v)}" for k, v in env.items() if k not in secret_keys]
        secret_lines = [f"export {k}={shlex.quote(v)}" for k, v in env.items() if k in secret_keys]
        script = " && ".join([
            # Non-secret env goes in .bashrc (TERM, GIT_USER_NAME, etc.)
            f"echo {shlex.quote(chr(10).join(safe_lines))} >> /home/dev/.bashrc",
            # Secrets written to tmpfs, sourced from .bashrc, auto-deleted on read
            f"echo {shlex.quote(chr(10).join(secret_lines))} > /dev/shm/.env",
            "chmod 600 /dev/shm/.env",
            "echo 'if [ -f /dev/shm/.env ]; then source /dev/shm/.env; rm -f /dev/shm/.env; fi' >> /home/dev/.bashrc",
            "source /home/dev/.bashrc",
            'git config --global user.name "${GIT_USER_NAME:-Claude Dev}"',
            'git config --global user.email "${GIT_USER_EMAIL:-dev@remolt.dev}"',
            'if [ -n "$REPO_URL" ]; then git clone "$REPO_URL" /home/dev/workspace 2>/dev/null || true; fi',
            'if [ ! -d /home/dev/remolt-dev ]; then git clone https://github.com/nthh/remolt.dev.git /home/dev/remolt-dev 2>/dev/null || true; fi',
            'mkdir -p /home/dev/.claude',
            """echo '{"permissions":{"allow":[],"deny":[]}}' > /home/dev/.claude/settings.json""",
        ])
        exec_inst = await container.exec(
            cmd=["bash", "-c", script],
        )
        await exec_inst.start(detach=True)

    async def relabel(self, sandbox_id: str, session_id: str, owner: str = "") -> None:
        pass  # Docker doesn't support label updates on running containers

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

    async def create(self, session_id: str, env: dict[str, str], *,
                     image: str | None = None, resources: dict | None = None,
                     ports: list[int] | None = None) -> str:
        # K8s names must be lowercase alphanumeric + hyphens, start/end with alphanumeric
        slug = session_id[:16].lower().replace("_", "-").strip("-")
        pod_name = f"remolt-{slug}"
        container_image = image or SANDBOX_IMAGE
        container_resources = resources or {
            "requests": {"cpu": "250m", "memory": "512Mi"},
            "limits": {"cpu": "2", "memory": "2Gi"},
        }
        container_ports = [{"containerPort": p} for p in (ports or [])]
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
                "annotations": {},  # bandwidth enforced via init container tc rule
            },
            "spec": {
                "hostname": "sandbox",
                "automountServiceAccountToken": False,
                "restartPolicy": "Never",
                "activeDeadlineSeconds": 14400,
                "initContainers": [{
                    "name": "bandwidth-limit",
                    "image": "alpine",
                    "command": ["sh", "-c",
                        f"apk add --no-cache iproute2 && "
                        f"DEV=$(ip route show default | awk '{{print $5}}' | head -1) && "
                        f"tc qdisc add dev $DEV root tbf rate {SANDBOX_BANDWIDTH} burst 256kbit latency 50ms"
                    ],
                    "securityContext": {
                        "capabilities": {"add": ["NET_ADMIN"]},
                    },
                }],
                "containers": [{
                    "name": "sandbox",
                    "image": container_image,
                    "tty": True,
                    "stdin": True,
                    "env": [{"name": k, "value": v} for k, v in env.items()],
                    "ports": container_ports,
                    "resources": container_resources,
                    "securityContext": {
                        "allowPrivilegeEscalation": True,
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
                "owner": labels.get("remolt.owner", ""),
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
            proxy=None,
        )
        return K8sExecStream(ws)

    async def inject_env(self, sandbox_id: str, env: dict[str, str]) -> None:
        import websockets
        from urllib.parse import quote

        secret_keys = {"GITHUB_TOKEN", "ANTHROPIC_API_KEY"}
        safe_lines = "\n".join(f"export {k}={shlex.quote(v)}" for k, v in env.items() if k not in secret_keys)
        secret_lines = "\n".join(f"export {k}={shlex.quote(v)}" for k, v in env.items() if k in secret_keys)
        script = (
            f"echo {shlex.quote(safe_lines)} >> /home/dev/.bashrc"
            f" && echo {shlex.quote(secret_lines)} > /dev/shm/.env"
            " && chmod 600 /dev/shm/.env"
            """ && echo 'if [ -f /dev/shm/.env ]; then source /dev/shm/.env; rm -f /dev/shm/.env; fi' >> /home/dev/.bashrc"""
            " && source /home/dev/.bashrc"
            ' && git config --global user.name "${GIT_USER_NAME:-Claude Dev}"'
            ' && git config --global user.email "${GIT_USER_EMAIL:-dev@remolt.dev}"'
            ' && if [ -n "$REPO_URL" ]; then git clone "$REPO_URL" /home/dev/workspace 2>/dev/null || true; fi'
            ' && if [ ! -d /home/dev/remolt-dev ]; then git clone https://github.com/nthh/remolt.dev.git /home/dev/remolt-dev 2>/dev/null || true; fi'
            ' && mkdir -p /home/dev/.claude'
            """ && echo '{"permissions":{"allow":[],"deny":[]}}' > /home/dev/.claude/settings.json"""
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
            proxy=None,
        ) as ws:
            # Wait for exec to complete
            async for frame in ws:
                pass

    async def relabel(self, sandbox_id: str, session_id: str, owner: str = "") -> None:
        labels: dict[str, str] = {"remolt.session-id": session_id}
        if owner:
            labels["remolt.owner"] = owner
        resp = await self._client.patch(
            f"/api/v1/namespaces/{self._namespace}/pods/{sandbox_id}",
            json={"metadata": {"labels": labels}},
            headers={"Content-Type": "application/merge-patch+json"},
        )
        if resp.status_code != 200:
            logger.warning(f"Failed to relabel pod {sandbox_id}: {resp.status_code}")

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
        if not sb["running"] or not sid or sid.startswith("warm-"):
            await backend.destroy(sb["id"])
            logger.info(f"Cleaned up orphan sandbox {sb['id']}")
            continue
        meta = saved.get(sid, {})
        owner = sb.get("owner") or meta.get("owner", "")
        if not owner:
            await backend.destroy(sb["id"])
            logger.info(f"Destroyed ownerless session {sid} (sandbox {sb['id']})")
            continue
        sessions[sid] = Session(
            session_id=sid,
            sandbox_id=sb["id"],
            created_at=meta.get("created_at", time.time()),
            last_activity=time.time(),
            status=Status.RUNNING,
            has_repo=meta.get("has_repo", False),
            owner=owner,
            agent_type=meta.get("agent_type", "claude-code"),
        )
        live_sids.add(sid)
        logger.info(f"Recovered session {sid}")
        emit("session.recovered", session_id=sid)

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
    check_counter = 0
    while True:
        check_counter += 1

        # Every ~30s, validate pool health: destroy errored pods, purge stale queue entries
        if check_counter % 6 == 0:
            try:
                managed = await backend.list_managed()
                running_ids = set()
                for sb in managed:
                    sid = sb.get("session_id", "")
                    if sid.startswith("warm-") and not sb["running"]:
                        logger.info(f"Warm pool: destroying errored pod {sb['id']}")
                        await backend.destroy(sb["id"])
                    elif sb["running"]:
                        running_ids.add(sb["id"])

                # Purge queue entries pointing to dead pods
                requeue = []
                while not warm_pool.empty():
                    try:
                        sbid = warm_pool.get_nowait()
                        if sbid in running_ids:
                            requeue.append(sbid)
                        else:
                            logger.info(f"Warm pool: dropping stale queue entry {sbid}")
                    except asyncio.QueueEmpty:
                        break
                for sbid in requeue:
                    warm_pool.put_nowait(sbid)
            except Exception as e:
                logger.warning(f"Warm pool health check failed: {e}")

        # Only warm-pool agents that have warm_pool=true (default: claude-code)
        warm_agent = AGENTS.get("claude-code")
        warm_image = _agent_image("claude-code") if warm_agent else SANDBOX_IMAGE
        deficit = WARM_POOL_SIZE - warm_pool.qsize()
        for _ in range(deficit):
            try:
                pool_id = secrets.token_hex(4)
                sandbox_id = await backend.create(
                    f"warm-{pool_id}", {"TERM": "xterm-256color"},
                    image=warm_image,
                )
                warm_pool.put_nowait(sandbox_id)
                logger.info(f"Warm pool: created {sandbox_id} (pool size: {warm_pool.qsize()})")
                emit("warm_pool.created", sandbox_id=sandbox_id, pool_size=warm_pool.qsize())
            except Exception as e:
                logger.warning(f"Warm pool: failed to create sandbox: {e}")
                break
        await asyncio.sleep(5)


async def claim_warm_sandbox(session_id: str, env: dict[str, str]) -> str | None:
    """Try to claim a pre-warmed sandbox. Returns sandbox_id or None."""
    try:
        sandbox_id = warm_pool.get_nowait()
    except asyncio.QueueEmpty:
        return None
    try:
        await backend.inject_env(sandbox_id, env)
        await backend.relabel(sandbox_id, session_id)
        logger.info(f"Claimed warm sandbox {sandbox_id} (pool size: {warm_pool.qsize()})")
        emit("warm_pool.claimed", sandbox_id=sandbox_id, pool_size=warm_pool.qsize())
        return sandbox_id
    except Exception as e:
        logger.warning(f"Warm sandbox {sandbox_id} unusable, destroying: {e}")
        try:
            await backend.destroy(sandbox_id)
        except Exception:
            pass
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
    # Drain warm pool (unused pre-warmed pods) but keep session pods alive
    # for recovery after restart. Session pods have remolt.session-id labels
    # and will be reclaimed by recover_sessions() on next startup.
    while not warm_pool.empty():
        try:
            sandbox_id = warm_pool.get_nowait()
            await backend.destroy(sandbox_id)
        except Exception:
            pass
    save_sessions()
    await backend.close()
    emit("server.stopped", preserved_sessions=len(sessions))
    logger.info(f"Remolt server stopped — {len(sessions)} session(s) preserved for recovery")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="Remolt", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

from cryptography.fernet import Fernet, InvalidToken

_fernet_key = base64.urlsafe_b64encode(hashlib.sha256(COOKIE_SECRET.encode()).digest())
_fernet = Fernet(_fernet_key)


def _encrypt_cookie(data: dict) -> str:
    return _fernet.encrypt(_json.dumps(data).encode()).decode()


def _decrypt_cookie(token: str) -> dict | None:
    try:
        return _json.loads(_fernet.decrypt(token.encode()))
    except (InvalidToken, Exception):
        return None


@dataclass
class AuthUser:
    login: str
    name: str
    email: str
    gh_token: str


async def get_current_user(request) -> AuthUser | None:
    """Extract authenticated user from cookie. Returns None if not authed."""
    if not AUTH_REQUIRED:
        return None  # Auth disabled, all requests allowed
    cookie = request.cookies.get("remolt_auth")
    if not cookie:
        return None
    data = _decrypt_cookie(cookie)
    if not data:
        return None
    try:
        return AuthUser(**data)
    except Exception:
        return None


def require_auth(request) -> AuthUser:
    """Raise 401 if not authenticated."""
    if not AUTH_REQUIRED:
        return AuthUser(login="anonymous", name="", email="", gh_token="")
    cookie = request.cookies.get("remolt_auth")
    if not cookie:
        raise HTTPException(401, "Authentication required")
    data = _decrypt_cookie(cookie)
    if not data:
        raise HTTPException(401, "Invalid session")
    try:
        return AuthUser(**data)
    except Exception:
        raise HTTPException(401, "Invalid session")


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------

@app.get("/auth/login")
async def auth_login(repo: bool = False):
    if not GITHUB_CLIENT_ID:
        raise HTTPException(501, "GitHub OAuth not configured")
    state = secrets.token_urlsafe(16)
    scope = "read:user user:email public_repo"
    if repo:
        scope += " repo"
    url = (
        f"https://github.com/login/oauth/authorize"
        f"?client_id={GITHUB_CLIENT_ID}"
        f"&scope={scope}"
        f"&state={state}"
    )
    resp = RedirectResponse(url)
    resp.set_cookie("oauth_state", state, httponly=True, secure=True, samesite="lax", max_age=600)
    return resp


@app.get("/auth/callback")
async def auth_callback(request: Request, code: str = "", state: str = ""):
    if not GITHUB_CLIENT_ID or not GITHUB_CLIENT_SECRET:
        raise HTTPException(501, "GitHub OAuth not configured")

    saved_state = request.cookies.get("oauth_state")
    if not saved_state or not hmac.compare_digest(saved_state, state):
        raise HTTPException(400, "Invalid OAuth state")

    # Exchange code for token
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            "https://github.com/login/oauth/access_token",
            json={
                "client_id": GITHUB_CLIENT_ID,
                "client_secret": GITHUB_CLIENT_SECRET,
                "code": code,
            },
            headers={"Accept": "application/json"},
        )
        if token_resp.status_code != 200:
            raise HTTPException(502, "GitHub token exchange failed")
        token_data = token_resp.json()
        gh_token = token_data.get("access_token", "")
        if not gh_token:
            raise HTTPException(502, f"GitHub OAuth error: {token_data.get('error_description', 'unknown')}")

        # Fetch user info
        user_resp = await client.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {gh_token}", "Accept": "application/json"},
        )
        if user_resp.status_code != 200:
            raise HTTPException(502, "Failed to fetch GitHub user")
        user = user_resp.json()

        # Email may be private — fetch from /user/emails
        email = user.get("email", "") or ""
        if not email:
            emails_resp = await client.get(
                "https://api.github.com/user/emails",
                headers={"Authorization": f"Bearer {gh_token}", "Accept": "application/json"},
            )
            if emails_resp.status_code == 200:
                for e in emails_resp.json():
                    if e.get("primary") and e.get("verified"):
                        email = e.get("email", "")
                        break

    resp = RedirectResponse("/?authed=1")
    resp.set_cookie(
        "remolt_auth", _encrypt_cookie({
            "login": user.get("login", ""),
            "name": user.get("name", "") or "",
            "email": email,
            "gh_token": gh_token,
        }),
        httponly=True, secure=True, samesite="lax", max_age=86400,
    )
    resp.delete_cookie("oauth_state")
    emit("auth.login", login=user.get("login", ""))
    return resp


@app.get("/auth/me")
async def auth_me(request: Request):
    user = require_auth(request)
    return {
        "login": user.login,
        "name": user.name,
        "email": user.email,
        "auth_required": AUTH_REQUIRED,
    }


@app.get("/auth/logout")
async def auth_logout():
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie("remolt_auth")
    return resp


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------


class CreateSessionReq(BaseModel):
    repo_url: str | None = None
    git_user_name: str | None = None
    git_user_email: str | None = None
    api_key: str | None = None
    github_token: str | None = None
    agent_type: str | None = None
    agent_env: dict[str, str] | None = None


class SessionResp(BaseModel):
    session_id: str
    status: str
    ws_url: str
    agent_type: str = "claude-code"
    proxy_url: str | None = None


@app.get("/health")
async def health():
    return {"status": "ok", "sessions": len(sessions), "version": VERSION}


@app.post("/api/sessions", response_model=SessionResp)
async def api_create_session(request: Request, body: CreateSessionReq):
    user = require_auth(request)

    if len(sessions) >= MAX_SESSIONS:
        raise HTTPException(429, "Max sessions reached")

    if AUTH_REQUIRED:
        user_count = sum(1 for s in sessions.values() if s.owner == user.login and s.status == Status.RUNNING)
        if user_count >= MAX_USER_SESSIONS:
            raise HTTPException(429, f"Max {MAX_USER_SESSIONS} sessions per user")

    # Resolve agent type
    agent_type = body.agent_type or "claude-code"
    agent = AGENTS.get(agent_type)
    if not agent:
        raise HTTPException(400, f"Unknown agent type: {agent_type}")

    sid = secrets.token_urlsafe(32)
    env: dict[str, str] = {"TERM": "xterm-256color"}
    if body.repo_url:
        env["REPO_URL"] = body.repo_url
    if body.git_user_name:
        env["GIT_USER_NAME"] = body.git_user_name
    elif user.name:
        env["GIT_USER_NAME"] = user.name
    if body.git_user_email:
        env["GIT_USER_EMAIL"] = body.git_user_email
    elif user.email:
        env["GIT_USER_EMAIL"] = user.email
    # Inject GitHub token: prefer OAuth token, fall back to manually-provided PAT
    gh_token = user.gh_token or body.github_token
    if gh_token:
        env["GITHUB_TOKEN"] = gh_token
    if body.api_key:
        env["ANTHROPIC_API_KEY"] = body.api_key

    # Inject agent-specific env defaults
    for k, v in agent.env_defaults.items():
        env.setdefault(k, v)

    # Inject agent-specific env vars from request
    if body.agent_env:
        # Only allow keys defined in agent's env_schema
        allowed_keys = {e["key"] for e in agent.env_schema}
        for k, v in body.agent_env.items():
            if k in allowed_keys and v:
                env[k] = v

    # Inject agent setup and welcome as env vars for entrypoint.sh
    if agent.setup:
        env["AGENT_SETUP"] = agent.setup
    if agent.welcome:
        env["AGENT_WELCOME"] = agent.welcome

    SECRET_KEYS = {"GITHUB_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"}
    safe_env = {k: v for k, v in env.items() if k not in SECRET_KEYS}

    image = _agent_image(agent_type)
    agent_ports = [p.port for p in agent.ports]

    try:
        # Only use warm pool for warm-pool-eligible agents
        sandbox_id = None
        if agent.warm_pool:
            sandbox_id = await claim_warm_sandbox(sid, env)
        if not sandbox_id:
            # Cold start: create with safe env only, then inject secrets via exec
            sandbox_id = await backend.create(
                sid, safe_env, image=image,
                resources=agent.resources, ports=agent_ports,
            )
            await backend.inject_env(sandbox_id, env)
    except Exception as e:
        logger.error(f"Failed to create sandbox: {e}")
        raise HTTPException(500, "Failed to create session. Please try again.")

    sessions[sid] = Session(
        session_id=sid,
        sandbox_id=sandbox_id,
        status=Status.RUNNING,
        has_repo=bool(body.repo_url),
        owner=user.login,
        agent_type=agent_type,
    )
    # Persist owner in pod labels so sessions survive server restarts
    try:
        await backend.relabel(sandbox_id, sid, owner=user.login)
    except Exception:
        pass  # Non-fatal: session works, just won't survive a restart with owner info
    emit("session.created", session_id=sid, has_repo=bool(body.repo_url),
         user=user.login, agent_type=agent_type)
    save_sessions()

    proxy_url = f"/proxy/{sid}/" if agent.ports else None
    return SessionResp(
        session_id=sid, status="running", ws_url=f"/ws/terminal/{sid}",
        agent_type=agent_type, proxy_url=proxy_url,
    )


@app.get("/api/sessions/{session_id}", response_model=SessionResp)
async def api_get_session(request: Request, session_id: str):
    user = require_auth(request)
    s = sessions.get(session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    if AUTH_REQUIRED and s.owner and s.owner != user.login:
        raise HTTPException(403, "Not authorized")
    agent = AGENTS.get(s.agent_type)
    proxy_url = f"/proxy/{s.session_id}/" if agent and agent.ports else None
    return SessionResp(
        session_id=s.session_id, status=s.status.value, ws_url=f"/ws/terminal/{s.session_id}",
        agent_type=s.agent_type, proxy_url=proxy_url,
    )


@app.delete("/api/sessions/{session_id}")
async def api_delete_session(request: Request, session_id: str):
    user = require_auth(request)
    s = sessions.get(session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    if AUTH_REQUIRED and s.owner and s.owner != user.login:
        raise HTTPException(403, "Not authorized")
    sessions.pop(session_id)
    duration = time.time() - s.created_at
    await backend.destroy(s.sandbox_id)
    emit("session.ended", session_id=session_id, reason="user", duration_s=round(duration))
    save_sessions()
    return {"status": "terminated", "session_id": session_id}


# ---------------------------------------------------------------------------
# Agent list endpoint
# ---------------------------------------------------------------------------


@app.get("/api/agents")
async def api_list_agents():
    return [
        {
            "id": a.id,
            "name": a.name,
            "description": a.description,
            "icon": a.icon,
            "has_dashboard": bool(a.ports),
            "env_schema": a.env_schema,
        }
        for a in AGENTS.values()
    ]


# ---------------------------------------------------------------------------
# HTTP proxy for agent web UIs
# ---------------------------------------------------------------------------


async def _resolve_sandbox_ip(sandbox_id: str) -> str:
    """Resolve the IP address of a sandbox container/pod."""
    assert backend is not None
    if isinstance(backend, K8sBackend):
        pod_resp = await backend._client.get(
            f"/api/v1/namespaces/{backend._namespace}/pods/{sandbox_id}"
        )
        if pod_resp.status_code != 200:
            raise HTTPException(502, "Cannot resolve pod")
        ip = pod_resp.json().get("status", {}).get("podIP")
    elif isinstance(backend, DockerBackend):
        container = backend._docker.containers.container(sandbox_id)
        info = await container.show()
        networks = info.get("NetworkSettings", {}).get("Networks", {})
        ip = None
        for net in networks.values():
            ip = net.get("IPAddress")
            if ip:
                break
    else:
        raise HTTPException(501, "Proxy not supported for this backend")
    if not ip:
        raise HTTPException(502, "Cannot resolve sandbox IP")
    return ip


def _validate_proxy_access(request, session_id: str) -> tuple[Session, int]:
    """Validate auth and return (session, port) for proxy routes."""
    user = require_auth(request)
    s = sessions.get(session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    if AUTH_REQUIRED and s.owner and s.owner != user.login:
        raise HTTPException(403, "Not authorized")
    agent = AGENTS.get(s.agent_type)
    if not agent or not agent.ports:
        raise HTTPException(400, "This agent has no web UI")
    return s, agent.ports[0].port


_LOADING_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Starting...</title>
<meta http-equiv="refresh" content="3">
<style>body{background:#1a1b26;color:#a9b1d6;font-family:system-ui;display:flex;
align-items:center;justify-content:center;height:100vh;margin:0}
.wrap{text-align:center}.spinner{width:32px;height:32px;border:3px solid #33467c;
border-top-color:#7aa2f7;border-radius:50%;animation:spin 1s linear infinite;margin:0 auto 16px}
@keyframes spin{to{transform:rotate(360deg)}}</style></head>
<body><div class="wrap"><div class="spinner"></div>Starting dashboard...</div></body></html>"""


@app.api_route("/proxy/{session_id}/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy_agent_ui(request: Request, session_id: str, path: str = ""):
    s, port = _validate_proxy_access(request, session_id)
    ip = await _resolve_sandbox_ip(s.sandbox_id)
    target_url = f"http://{ip}:{port}/{path}"
    body = await request.body()
    headers = dict(request.headers)
    headers.pop("host", None)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.request(
                method=request.method,
                url=target_url,
                content=body,
                headers=headers,
                params=dict(request.query_params),
            )
    except (httpx.ConnectError, httpx.ConnectTimeout):
        # Gateway not ready yet — return loading page that auto-refreshes
        return Response(content=_LOADING_HTML, status_code=200,
                        media_type="text/html")
    content = resp.content
    resp_headers = dict(resp.headers)
    # Rewrite base path in dashboard HTML for relative asset loading
    ct = resp_headers.get("content-type", "")
    if "text/html" in ct and b"__OPENCLAW_CONTROL_UI_BASE_PATH__" in content:
        base = f"/proxy/{session_id}/"
        content = content.replace(
            b'__OPENCLAW_CONTROL_UI_BASE_PATH__=""',
            f'__OPENCLAW_CONTROL_UI_BASE_PATH__="{base}"'.encode(),
        )
        resp_headers.pop("content-length", None)
    return Response(
        content=content,
        status_code=resp.status_code,
        headers=resp_headers,
    )


@app.websocket("/proxy/{session_id}/{path:path}")
async def ws_proxy_agent(ws: WebSocket, session_id: str, path: str = ""):
    import websockets
    from urllib.parse import urlencode

    s, port = _validate_proxy_access(ws, session_id)
    ip = await _resolve_sandbox_ip(s.sandbox_id)
    qs = urlencode(dict(ws.query_params)) if ws.query_params else ""
    target_url = f"ws://{ip}:{port}/{path}{'?' + qs if qs else ''}"

    await ws.accept()

    # Forward Origin header so upstream (e.g. OpenClaw) can validate it
    extra_headers = {}
    origin = ws.headers.get("origin")
    if origin:
        extra_headers["Origin"] = origin

    try:
        async with websockets.connect(target_url, proxy=None, additional_headers=extra_headers) as upstream:
            async def client_to_upstream():
                try:
                    while True:
                        msg = await ws.receive()
                        if msg["type"] == "websocket.disconnect":
                            break
                        if "text" in msg and msg["text"]:
                            await upstream.send(msg["text"])
                        elif "bytes" in msg and msg["bytes"]:
                            await upstream.send(msg["bytes"])
                except (WebSocketDisconnect, asyncio.CancelledError):
                    pass

            async def upstream_to_client():
                try:
                    async for data in upstream:
                        if isinstance(data, str):
                            await ws.send_text(data)
                        else:
                            await ws.send_bytes(data)
                except (WebSocketDisconnect, asyncio.CancelledError):
                    pass

            await asyncio.gather(
                asyncio.create_task(client_to_upstream()),
                asyncio.create_task(upstream_to_client()),
                return_exceptions=True,
            )
    except Exception as e:
        logger.warning(f"WS proxy error for {session_id}: {e}")
    finally:
        try:
            await ws.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Root WebSocket proxy — routes dashboard WS by auth cookie
# ---------------------------------------------------------------------------


@app.websocket("/")
async def ws_root_proxy(ws: WebSocket):
    """Route root WebSocket to user's active dashboard session.

    Dashboard agents (OpenClaw, etc.) connect WebSocket to wss://hostname/.
    We resolve the user's active dashboard session from their auth cookie
    and proxy the connection to the pod.
    """
    import websockets
    from urllib.parse import urlencode

    # Reject cross-origin WebSocket connections
    origin = ws.headers.get("origin")
    if origin and origin not in ALLOWED_ORIGINS:
        await ws.close(code=4003, reason="Invalid origin")
        return

    user = await get_current_user(ws)
    if not user or not user.login:
        await ws.close(code=4003, reason="Not authorized")
        return

    # Find user's active session with a dashboard port
    s = next(
        (s for s in sessions.values()
         if s.owner == user.login
         and s.status == Status.RUNNING
         and AGENTS.get(s.agent_type) and AGENTS[s.agent_type].ports),
        None,
    )
    if not s:
        await ws.close(code=4004, reason="No active dashboard session")
        return

    agent = AGENTS[s.agent_type]
    port = agent.ports[0].port

    try:
        ip = await _resolve_sandbox_ip(s.sandbox_id)
    except Exception:
        await ws.close(code=4005, reason="Cannot resolve sandbox")
        return

    # Build target URL with safe query string encoding
    qs = urlencode(dict(ws.query_params)) if ws.query_params else ""
    target_url = f"ws://{ip}:{port}/{'?' + qs if qs else ''}"

    await ws.accept()

    # Forward Origin header so upstream (e.g. OpenClaw) can validate it
    extra_headers = {}
    if origin:
        extra_headers["Origin"] = origin

    try:
        async with websockets.connect(target_url, proxy=None, additional_headers=extra_headers) as upstream:
            async def client_to_upstream():
                try:
                    while True:
                        msg = await ws.receive()
                        if msg["type"] == "websocket.disconnect":
                            break
                        if "text" in msg and msg["text"]:
                            await upstream.send(msg["text"])
                        elif "bytes" in msg and msg["bytes"]:
                            await upstream.send(msg["bytes"])
                except (WebSocketDisconnect, asyncio.CancelledError):
                    pass

            async def upstream_to_client():
                try:
                    async for data in upstream:
                        if isinstance(data, str):
                            await ws.send_text(data)
                        else:
                            await ws.send_bytes(data)
                except (WebSocketDisconnect, asyncio.CancelledError):
                    pass

            await asyncio.gather(
                asyncio.create_task(client_to_upstream()),
                asyncio.create_task(upstream_to_client()),
                return_exceptions=True,
            )
    except Exception as e:
        logger.warning(f"Root WS proxy error for {s.session_id}: {e}")
    finally:
        try:
            await ws.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# WebSocket terminal bridge
# ---------------------------------------------------------------------------


@app.websocket("/ws/terminal/{session_id}")
async def ws_terminal(ws: WebSocket, session_id: str):
    # Reject cross-origin WebSocket connections
    origin = ws.headers.get("origin")
    if origin and origin not in ALLOWED_ORIGINS:
        await ws.close(code=4003, reason="Invalid origin")
        return

    s = sessions.get(session_id)
    if not s or s.status != Status.RUNNING:
        await ws.close(code=4004, reason="Session not found")
        return

    # Verify the connecting user owns this session
    if AUTH_REQUIRED:
        user = await get_current_user(ws)
        if not user:
            await ws.close(code=4003, reason="Not authorized")
            return
        if not s.owner or user.login != s.owner:
            await ws.close(code=4003, reason="Not authorized")
            return

    await ws.accept()
    assert backend is not None

    # Retry exec_attach — sandbox may still be starting
    stream = None
    for attempt in range(10):
        try:
            stream = await backend.exec_attach(s.sandbox_id)
            break
        except Exception as e:
            if attempt < 9:
                logger.info(f"Session {session_id}: exec_attach attempt {attempt + 1}/10 failed: {e}")
                await asyncio.sleep(2)
            else:
                logger.error(f"Session {session_id}: exec_attach failed after 10 attempts: {e}")
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
        file = (_static / path).resolve()
        if file.is_file() and str(file).startswith(str(_static.resolve())):
            return FileResponse(file)
        return FileResponse(_static / "index.html")
