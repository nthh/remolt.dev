# remolt.dev

Sandboxed AI coding sessions in your browser. You are running inside a remolt sandbox right now.

## Architecture

```
Browser (React + xterm.js)
    ↕ WebSocket (binary TTY I/O + JSON control frames)
Server (FastAPI, single file: server/server.py)
    ↕ Docker API (local) or K8s API (production)
Sandbox Container/Pod (Ubuntu 24.04 + tmux + Claude Code + git + gh)
```

**Single-file server.** Everything is in `server/server.py` — sessions, WebSocket bridge, Docker backend, K8s backend, OAuth, cleanup. This is intentional. Don't split it.

**Single-page app.** Frontend is in `app/src/` — React + xterm.js + Vite. The server serves the built SPA from `/app/static`.

## Key Files

| File | What |
|------|------|
| `server/server.py` | The entire backend (FastAPI) |
| `container/Dockerfile` | Sandbox image (Ubuntu + Node + Claude + gh) |
| `container/entrypoint.sh` | Git config, repo clone, Claude pre-config |
| `app/src/App.tsx` | App shell, auth flow, routing |
| `app/src/hooks/useTerminal.ts` | xterm.js + WebSocket + auth URL detection |
| `app/src/contexts/SessionContext.tsx` | Session lifecycle, REST API |
| `app/src/components/SetupForm.tsx` | Launch form |
| `app/src/components/TerminalView.tsx` | Terminal + auth banner |
| `k8s/` | K8s manifests (namespace, RBAC, server deployment) |

## How It Works

1. User signs in with GitHub OAuth
2. Server creates a sandbox (Docker container or K8s Pod)
3. GitHub token injected as `GITHUB_TOKEN` — git push and gh CLI work immediately
4. Browser connects via WebSocket to tmux session inside sandbox
5. User runs `claude` — authenticates via browser OAuth
6. Sessions auto-destroyed after 1 hour idle

## Fixing Bugs

You're inside a remolt sandbox with the remolt source code at `~/remolt-dev/`. If you encounter a bug or want to improve something:

1. The source is already cloned at `~/remolt-dev/`
2. Fix the issue
3. Create a PR: `cd ~/remolt-dev && gh pr create`

The GitHub token is already configured, so `gh` and `git push` work immediately.

### What You Can Fix

- **Frontend bugs** — `app/src/` (React/TypeScript)
- **Server bugs** — `server/server.py` (Python/FastAPI)
- **Sandbox setup** — `container/Dockerfile`, `container/entrypoint.sh`
- **K8s config** — `k8s/`

### Testing Locally (inside sandbox)

```bash
cd ~/remolt-dev

# Frontend
cd app && npm install && npm run build

# Server (read-only — can't run inside sandbox, but tests work)
cd server && pip install fastapi uvicorn[standard] aiodocker httpx websockets
python -m pytest ../tests/
```

## Conventions

- Keep `server/server.py` as a single file
- Keep the sandbox image minimal — users have `sudo` to install what they need
- Tokyo Night color theme everywhere
- No external databases — session state is in-memory + `sessions.json`
