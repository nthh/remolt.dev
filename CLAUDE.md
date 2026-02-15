# remolt.dev

Sandboxed AI coding sessions in your browser. You are running inside a remolt sandbox right now.

## Architecture

```
Browser (React + xterm.js)
    ↕ WebSocket (binary TTY I/O + JSON control frames)
    ↕ HTTP/WS proxy (agent dashboards like OpenClaw)
Server (FastAPI, single file: server/server.py)
    ↕ Docker API (local) or K8s API (production)
Sandbox Container/Pod (Ubuntu 24.04 + tmux + agent CLI + git + gh)
```

**Single-file server.** Everything is in `server/server.py` — sessions, WebSocket bridge, Docker backend, K8s backend, OAuth, agent proxy, cleanup. This is intentional. Don't split it.

**Single-page app.** Frontend is in `app/src/` — React + xterm.js + Vite. The server serves the built SPA from `/app/static`.

**Agent plugin system.** Each agent (Claude Code, OpenClaw, etc.) is defined by a JSON config in `agents/{id}/agent.json`. The server loads these at startup and uses them for image selection, env injection, resource limits, and dashboard proxying.

## Key Files

| File | What |
|------|------|
| `server/server.py` | The entire backend (FastAPI) |
| `agents/*/agent.json` | Agent plugin configs (image, ports, setup, env) |
| `container/Dockerfile.base` | Shared base image (Ubuntu + Node + git + gh) |
| `container/Dockerfile.agent` | Per-agent image layer (installs agent CLI) |
| `container/entrypoint.sh` | Git config, repo clone, agent setup |
| `app/src/App.tsx` | App shell, auth flow, routing |
| `app/src/hooks/useTerminal.ts` | xterm.js + WebSocket + auth URL detection |
| `app/src/contexts/SessionContext.tsx` | Session lifecycle, REST API |
| `app/src/components/SetupForm.tsx` | Launch form (agent selector) |
| `app/src/components/TerminalView.tsx` | Terminal + auth banner + dashboard button |
| `k8s/` | K8s manifests (namespace, RBAC, server deployment) |

## How It Works

1. User signs in with GitHub OAuth
2. User selects an agent (Claude Code, OpenClaw, etc.) and optionally a repo
3. Server creates a sandbox (Docker container or K8s Pod) with the agent's image
4. GitHub token + agent env vars injected into sandbox
5. Browser connects via WebSocket to tmux session inside sandbox
6. For dashboard-based agents (OpenClaw), server proxies HTTP + WebSocket to agent port
7. Sessions auto-destroyed after 1 hour idle

## Fixing Bugs & Requesting Features

You're inside a remolt sandbox with the remolt source code at `~/remolt-dev/`. If you encounter a bug, want to improve something, or have a feature request:

1. The source is already cloned at `~/remolt-dev/`
2. **Bug fix:** Fix the issue and create a PR: `cd ~/remolt-dev && gh pr create`
3. **Feature request:** File an issue: `cd ~/remolt-dev && gh issue create`

The GitHub token is already configured, so `gh`, `git push`, and `gh issue create` work immediately.

### What You Can Fix

- **Frontend bugs** — `app/src/` (React/TypeScript)
- **Server bugs** — `server/server.py` (Python/FastAPI)
- **Sandbox setup** — `container/Dockerfile.base`, `container/Dockerfile.agent`, `container/entrypoint.sh`
- **Agent configs** — `agents/*/agent.json`
- **K8s config** — `k8s/`

### Testing Locally (inside sandbox)

```bash
cd ~/remolt-dev

# Frontend
cd app && npm install && npm run build

# Server (read-only — can't run inside sandbox, but tests work)
cd server && pip install fastapi uvicorn[standard] aiodocker httpx websockets cryptography
python -m pytest ../tests/
```

## Conventions

- Keep `server/server.py` as a single file
- Keep the sandbox image minimal — users have `sudo` to install what they need
- Tokyo Night color theme everywhere
- No external databases — session state is in-memory + `sessions.json`
