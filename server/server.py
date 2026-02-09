"""
Remolt server — session manager + WebSocket-to-TTY bridge.

Single-file FastAPI server that:
1. Creates sandbox containers on demand (POST /api/sessions)
2. Bridges browser WebSocket ↔ container TTY (WS /ws/terminal/{id})
3. Cleans up idle containers (background loop)
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
import os
import sys
import time
import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum

from pathlib import Path

import aiodocker
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SANDBOX_IMAGE = os.getenv("REMOLT_SANDBOX_IMAGE", "remolt-sandbox")
STATIC_DIR = os.getenv("REMOLT_STATIC_DIR", "")  # path to built frontend (dist/)
MAX_IDLE_SECONDS = int(os.getenv("REMOLT_MAX_IDLE_SECONDS", "3600"))
CLEANUP_INTERVAL = int(os.getenv("REMOLT_CLEANUP_INTERVAL", "60"))
MAX_SESSIONS = int(os.getenv("REMOLT_MAX_SESSIONS", "10"))

logger = logging.getLogger("remolt")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)

# ---------------------------------------------------------------------------
# Analytics — structured JSON events to stdout
# ---------------------------------------------------------------------------


EVENTS_LOG = os.getenv("REMOLT_EVENTS_LOG", "")  # optional file path for durable analytics
SESSIONS_FILE = os.getenv("REMOLT_SESSIONS_FILE", "")  # optional file path for session persistence


def emit(event: str, **data) -> None:
    """Write a structured analytics event to stdout + optional file."""
    record = {
        "ts": time.time(),
        "event": event,
        **data,
    }
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
    """Persist session metadata to disk."""
    if not SESSIONS_FILE:
        return
    try:
        data = [
            {
                "session_id": s.session_id,
                "container_id": s.container_id,
                "network_id": s.network_id,
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
    """Load persisted session metadata from disk."""
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
    container_id: str
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    status: Status = Status.CREATING
    has_repo: bool = False
    network_id: str | None = None


sessions: dict[str, Session] = {}
docker: aiodocker.Docker | None = None

# ---------------------------------------------------------------------------
# Container lifecycle
# ---------------------------------------------------------------------------


async def create_container(
    session_id: str,
    env: dict[str, str],
) -> tuple[str, str]:
    """Create and start a sandbox container with an isolated network.

    Returns (container_id, network_id).
    """
    assert docker is not None

    # Create a dedicated bridge network for this session
    net_name = f"remolt-net-{session_id}"
    network = await docker.networks.create({"Name": net_name, "Driver": "bridge"})
    network_id = network.id
    logger.info(f"Created network {net_name}")

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
    container = await docker.containers.create_or_replace(
        name=f"remolt-{session_id}", config=config
    )
    await container.start()
    logger.info(f"Started container remolt-{session_id}")
    return container.id, network_id


async def destroy_container(container_id: str, network_id: str | None = None) -> None:
    """Stop and remove a container, then delete its network."""
    assert docker is not None
    try:
        c = docker.containers.container(container_id)
        await c.stop(t=5)
    except Exception:
        pass
    try:
        c = docker.containers.container(container_id)
        await c.delete(force=True)
    except Exception as e:
        logger.warning(f"Failed to remove container {container_id[:12]}: {e}")
    if network_id:
        try:
            await docker.networks.delete(network_id)
            logger.info(f"Deleted network {network_id[:12]}")
        except Exception as e:
            logger.warning(f"Failed to remove network {network_id[:12]}: {e}")


async def cleanup_loop() -> None:
    """Periodically destroy idle sessions."""
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
                await destroy_container(s.container_id, s.network_id)
                emit("session.ended", session_id=sid, reason="idle", duration_s=round(duration))
        if to_remove:
            save_sessions()


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------


async def recover_sessions() -> None:
    """On startup, restore sessions from disk + reconcile with running containers."""
    assert docker is not None

    # Load persisted session metadata
    saved = {s["session_id"]: s for s in load_sessions()}

    # Scan Docker for managed containers
    containers = await docker.containers.list(
        all=True, filters={"label": ["remolt.managed=true"]}
    )
    live_sids: set[str] = set()

    for c in containers:
        labels = c["Labels"] or {}
        sid = labels.get("remolt.session-id", "")
        state = c["State"]

        if state == "running" and sid:
            meta = saved.get(sid, {})
            sessions[sid] = Session(
                session_id=sid,
                container_id=c.id,
                created_at=meta.get("created_at", time.time()),
                last_activity=time.time(),  # reset idle clock on recovery
                status=Status.RUNNING,
                has_repo=meta.get("has_repo", False),
                network_id=meta.get("network_id"),
            )
            live_sids.add(sid)
            logger.info(f"Recovered session {sid}")
            emit("session.recovered", session_id=sid)
        else:
            # Dead container — clean up
            meta = saved.get(sid, {})
            try:
                await docker.containers.container(c.id).delete(force=True)
                logger.info(f"Cleaned up dead container {c.id[:12]}")
            except Exception:
                pass
            # Remove the dead container's network
            net_id = meta.get("network_id")
            if net_id:
                try:
                    await docker.networks.delete(net_id)
                    logger.info(f"Cleaned up orphaned network {net_id[:12]}")
                except Exception:
                    pass

    # Clean up any orphaned remolt-net-* networks not tied to a live session
    try:
        all_networks = await docker.networks.list()
        for net in all_networks:
            name = net.get("Name", "")
            if name.startswith("remolt-net-"):
                net_sid = name.removeprefix("remolt-net-")
                if net_sid not in live_sids:
                    try:
                        await docker.networks.delete(net["Id"])
                        logger.info(f"Cleaned up orphaned network {name}")
                    except Exception:
                        pass
    except Exception:
        pass

    if live_sids:
        save_sessions()
        logger.info(f"Recovered {len(live_sids)} session(s)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global docker
    docker = aiodocker.Docker()
    await recover_sessions()
    task = asyncio.create_task(cleanup_loop())
    logger.info("Remolt server started")
    emit("server.started", max_sessions=MAX_SESSIONS, max_idle_s=MAX_IDLE_SECONDS)
    yield
    task.cancel()
    # Destroy all sessions on shutdown
    for sid in list(sessions):
        s = sessions.pop(sid, None)
        if s:
            duration = time.time() - s.created_at
            await destroy_container(s.container_id, s.network_id)
            emit("session.ended", session_id=sid, reason="shutdown", duration_s=round(duration))
    await docker.close()
    emit("server.stopped")
    logger.info("Remolt server stopped")


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
    env: dict[str, str] = {
        "TERM": "xterm-256color",
    }
    if body.api_key:
        env["ANTHROPIC_API_KEY"] = body.api_key
    if body.repo_url:
        env["REPO_URL"] = body.repo_url
    if body.git_user_name:
        env["GIT_USER_NAME"] = body.git_user_name
    if body.git_user_email:
        env["GIT_USER_EMAIL"] = body.git_user_email

    try:
        cid, nid = await create_container(sid, env)
    except Exception as e:
        raise HTTPException(500, f"Failed to create container: {e}")

    sessions[sid] = Session(
        session_id=sid,
        container_id=cid,
        status=Status.RUNNING,
        has_repo=bool(body.repo_url),
        network_id=nid,
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
    await destroy_container(s.container_id, s.network_id)
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
    assert docker is not None
    container = docker.containers.container(s.container_id)

    # Attach to tmux session (creates on first connect, reattaches on reconnect)
    exec_inst = await container.exec(
        cmd=["bash", "-lc", "tmux new-session -As main"],
        stdin=True,
        stdout=True,
        stderr=True,
        tty=True,
        environment={"TERM": "xterm-256color", "COLUMNS": "120", "LINES": "30"},
    )

    # Start exec — returns a Stream (async context manager with read_out/write_in)
    exec_id = exec_inst._id
    stream = exec_inst.start(detach=False)

    async with stream:
        emit("terminal.connected", session_id=session_id)

        async def read_from_container():
            """Read container stdout → send as WS binary frames."""
            try:
                while True:
                    msg = await stream.read_out()
                    if msg is None:
                        logger.info(f"Session {session_id}: stream ended (read_out returned None)")
                        break
                    if msg.data:
                        await ws.send_bytes(msg.data)
                        s.last_activity = time.time()
            except (asyncio.CancelledError, WebSocketDisconnect):
                pass
            except Exception as e:
                logger.warning(f"Session {session_id}: container read error: {type(e).__name__}: {e}")

        async def read_from_client():
            """Read WS messages → write to container stdin."""
            try:
                while True:
                    msg = await ws.receive()
                    if msg["type"] == "websocket.disconnect":
                        break

                    if "bytes" in msg and msg["bytes"]:
                        await stream.write_in(msg["bytes"])
                        s.last_activity = time.time()

                    elif "text" in msg and msg["text"]:
                        import json
                        try:
                            ctrl = json.loads(msg["text"])
                            if ctrl.get("type") == "resize":
                                cols = ctrl.get("cols", 120)
                                rows = ctrl.get("rows", 30)
                                async with docker._query(
                                    f"exec/{exec_id}/resize",
                                    method="POST",
                                    params={"h": rows, "w": cols},
                                ):
                                    pass
                        except (json.JSONDecodeError, KeyError, Exception):
                            pass
                        s.last_activity = time.time()

            except WebSocketDisconnect:
                logger.info(f"Session {session_id}: client disconnected")
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.warning(f"Session {session_id}: client read error: {type(e).__name__}: {e}")

        container_task = asyncio.create_task(read_from_container())
        client_task = asyncio.create_task(read_from_client())

        try:
            done, pending = await asyncio.wait(
                [container_task, client_task], return_when=asyncio.FIRST_COMPLETED
            )
            for t in pending:
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass
        finally:
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
    # Serve /assets with hashed filenames (immutable cache)
    app.mount("/assets", StaticFiles(directory=_static / "assets"), name="assets")

    # SPA fallback — serve index.html for all other paths
    @app.get("/{path:path}")
    async def spa_fallback(path: str):
        file = _static / path
        if file.is_file():
            return FileResponse(file)
        return FileResponse(_static / "index.html")
