# Remolt

Sandboxed AI coding sessions in your browser. Connect your API key and a GitHub repo, get a full Linux terminal with Claude Code, Aider, or any AI coding CLI — push commits directly from the session.

**Live:** [remolt.dev](https://remolt.dev)
**License:** Apache 2.0

---

## Quick Start

```bash
# 1. Build the sandbox image
#    This is the Linux environment users get when they launch a session.
#    The server creates containers from this image at runtime via Docker API.
docker build -t remolt-sandbox container/

# 2. Build the Remolt server (includes the frontend)
docker build -t remolt .

# 3. Run
docker run -p 3000:8080 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v remolt-data:/data \
  -e REMOLT_EVENTS_LOG=/data/events.jsonl \
  -e REMOLT_SESSIONS_FILE=/data/sessions.json \
  remolt

# 4. Open http://localhost:3000
#    Click "Launch Session" → type `claude` → log in via browser when prompted
```

Two images because they serve different purposes: `remolt` is the server (runs once), `remolt-sandbox` is the template for user sessions (spawned on demand by the server via Docker API). The sandbox image must exist in the Docker image cache before the server can create session containers from it.

---

## How It Works

```
Browser (React + xterm.js)
    ↕ WebSocket (binary TTY I/O + JSON control frames)
Server (FastAPI, single file, serves SPA + API)
    ↕ Docker API (aiodocker)
Sandbox Container (Ubuntu 24.04 + tmux + Claude Code + git + gh)
```

1. User clicks "Launch Session" (optionally enters API key, GitHub PAT, repo URL)
2. Server creates a Docker container from the sandbox image
3. Browser connects via WebSocket to a tmux session inside the container
4. User runs `claude` — authenticates via browser OAuth (or uses a pre-set API key)
5. Claude can push commits via `gh` / `git push` using the provided PAT
6. Session auto-destroyed after 1 hour idle (configurable)

**No API key required.** Claude Code supports interactive login — run `claude` in the terminal and it shows a URL to visit in your browser. Alternatively, provide an `ANTHROPIC_API_KEY` in the form for headless use.

---

## Sessions

### Lifecycle

```
Launch Session
  → Container created with user's env vars
  → Entrypoint: GitHub auth → git clone → bash
  → Server execs: tmux new-session -As main
  → WebSocket bridges browser ↔ tmux TTY

Close browser tab
  → WebSocket disconnects, container keeps running
  → tmux session preserved (scrollback, running processes, Claude mid-conversation)
  → Idle timeout starts (default: 1 hour)

Reopen browser tab
  → Frontend finds session_id in localStorage
  → GET /api/sessions/{id} — still alive?
  → WebSocket reconnects → tmux reattaches
  → Terminal restored exactly as you left it

Idle > 1 hour (or click "End Session")
  → Container destroyed, session gone
```

### Reconnection

Three mechanisms make sessions durable:

**tmux** — The terminal session lives inside the container, independent of the WebSocket. Claude Code mid-conversation, running processes, scrollback — all survive disconnects. Reconnecting reattaches to the same tmux session.

**Auto-reconnect** — If the WebSocket drops (network blip, server restart), the browser retries with exponential backoff (1s → 2s → 4s → 8s → 16s, max 5 attempts). Combined with tmux, brief outages are invisible.

**Session persistence** — The server writes session metadata to disk (`sessions.json`). On restart, it scans Docker for containers labeled `remolt.managed=true`, reconciles with the saved metadata, and reclaims running sessions.

### What Survives What

| Event | Terminal state | Repo & files | Credentials |
|-------|---------------|--------------|-------------|
| Network blip | Preserved | Preserved | Preserved |
| Close & reopen tab | Preserved | Preserved | Preserved |
| Server restart | Preserved | Preserved | Preserved |
| Idle timeout (1hr) | Lost | Lost | Lost |
| Click "End Session" | Lost | Lost | Lost |

---

## What's Stored

### Server-side

| Data | Where | Lifetime |
|------|-------|----------|
| Session metadata | `/data/sessions.json` (mounted volume) | Until session ends |
| Analytics events | `/data/events.jsonl` (mounted volume) | Indefinite (append-only) |
| Container state | Docker | Until session ends or idle timeout |

**The server never stores API keys or tokens on disk.** They exist only in the container's environment variables and are destroyed with the container.

### Browser-side

| Data | Where | Lifetime |
|------|-------|----------|
| Session ID | `localStorage` (`remolt:session`) | Until session ends |
| Preferences | `localStorage` (`remolt:prefs`) | Indefinite |
| API keys / tokens | Not stored | Entered each browser session |

Preferences include repo URL, git name, and git email — convenience fields so you don't re-type them. API keys and tokens are never written to storage.

### Analytics Events

Structured JSON lines written to stdout (captured by Docker logs) and optionally to a file:

```jsonl
{"ts": 1739120400.0, "event": "server.started", "max_sessions": 10, "max_idle_s": 3600}
{"ts": 1739120410.5, "event": "session.created", "session_id": "a1b2c3", "has_github": true, "has_repo": true}
{"ts": 1739120411.2, "event": "terminal.connected", "session_id": "a1b2c3"}
{"ts": 1739120890.1, "event": "terminal.disconnected", "session_id": "a1b2c3"}
{"ts": 1739120890.3, "event": "session.ended", "session_id": "a1b2c3", "reason": "user", "duration_s": 480}
{"ts": 1739124000.0, "event": "session.recovered", "session_id": "x9y8z7"}
{"ts": 1739130000.0, "event": "server.stopped"}
```

| Event | When |
|-------|------|
| `server.started` | Server boots |
| `server.stopped` | Server shuts down |
| `session.created` | User launches a session |
| `session.ended` | Session destroyed (`reason`: `user`, `idle`, or `shutdown`) |
| `session.recovered` | Server restart reclaimed a running container |
| `terminal.connected` | Browser WebSocket connected |
| `terminal.disconnected` | Browser WebSocket disconnected |

---

## Supported Tools

The sandbox is a full Ubuntu 24.04 environment. Pre-installed:

| Tool | What |
|------|------|
| `claude` | [Claude Code](https://docs.anthropic.com/en/docs/claude-code) — Anthropic's AI coding CLI |
| `git` | Version control |
| `gh` | [GitHub CLI](https://cli.github.com/) — push, PR, issues |
| `node` / `npm` | Node.js 22 LTS |
| `tmux` | Terminal multiplexer (session persistence) |
| `sudo` | Install anything else |

Since you have `sudo`, install whatever you need:

```bash
# Python + Aider
sudo apt-get update && sudo apt-get install -y python3 python3-pip
pip3 install aider-chat

# Rust
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
```

### GitHub Integration

Provide a GitHub PAT and the sandbox gets:

- `gh` CLI authenticated — create PRs, browse issues, manage releases
- `git push` to any repo the token has access to
- Automatic repo clone into `/home/dev/workspace` if you provide a repo URL

**Required PAT scopes:** `repo` (for private repos) or none (for public repos).
[Create a PAT →](https://github.com/settings/tokens/new?scopes=repo&description=Remolt)

---

## Configuration

Environment variables on the server container:

| Variable | Default | Description |
|----------|---------|-------------|
| `REMOLT_SANDBOX_IMAGE` | `remolt-sandbox` | Docker image for session containers |
| `REMOLT_MAX_SESSIONS` | `10` | Max concurrent sessions |
| `REMOLT_MAX_IDLE_SECONDS` | `3600` | Idle timeout before cleanup (seconds) |
| `REMOLT_CLEANUP_INTERVAL` | `60` | Seconds between idle-check sweeps |
| `REMOLT_EVENTS_LOG` | _(none)_ | File path for durable analytics |
| `REMOLT_SESSIONS_FILE` | _(none)_ | File path for session persistence |
| `REMOLT_STATIC_DIR` | _(none)_ | Path to built frontend (`/app/static` in Docker image) |

---

## Development

For local iteration without Docker rebuilds:

```bash
# Terminal 1: server (needs Docker running for sandbox containers)
cd server
pip install fastapi uvicorn[standard] aiodocker
uvicorn server:app --port 8080 --reload

# Terminal 2: frontend (hot-reload, proxies /api and /ws to server)
cd app
npm install
npm run dev
# Open http://localhost:5173
```

The Vite dev server proxies `/api/*` and `/ws/*` to the server at `localhost:8080`.

---

## Deployment

### Production (remolt.dev)

| Component | Where |
|-----------|-------|
| Frontend SPA | Cloudflare Pages at `remolt.dev` |
| Server | Vultr K8s, `remolt` namespace |
| Sandbox containers | Vultr K8s Pods |
| Analytics + sessions | 1Gi PVC |

### Deploy to K8s

```bash
# Build and push images
docker build -t your-registry/remolt-sandbox container/
docker build -t your-registry/remolt .
docker push your-registry/remolt-sandbox
docker push your-registry/remolt

# Update image refs in k8s/server.yaml, then:
kubectl apply -f k8s/
```

K8s manifests create: `remolt` namespace, RBAC for Pod management, server Deployment + Service, 1Gi PVC for data.

### Deploy to a VM

```bash
# Both images need to exist on the host — the server creates sandbox
# containers at runtime, so remolt-sandbox must be in the local cache.
docker build -t remolt-sandbox container/
docker build -t remolt .

docker run -d --restart unless-stopped \
  -p 3000:8080 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v remolt-data:/data \
  -e REMOLT_EVENTS_LOG=/data/events.jsonl \
  -e REMOLT_SESSIONS_FILE=/data/sessions.json \
  remolt
```

Put a reverse proxy (Caddy, nginx) in front for HTTPS.

---

## Security

### Isolation

- Each session is an isolated container with no shared filesystem
- Containers run as non-root user `dev` (with sudo for convenience)
- No access to host network, other containers, or server filesystem
- Server communicates with containers only via Docker exec API

### Credentials

- API keys and tokens are container env vars — never written to server disk
- Destroyed when the container is removed
- Browser stores only session ID and non-secret preferences

### Threat Model

Remolt is for **trusted users with their own API keys**. It is not a multi-tenant platform. If deploying publicly:

- Users can run arbitrary code (by design — it's a dev environment)
- Mitigate resource abuse with `REMOLT_MAX_SESSIONS` and K8s resource limits
- Docker socket mount gives the server Docker access — mitigate with gVisor or Pod Security Standards in K8s

---

## Architecture

```
experiments/remote-dev/
├── README.md
├── Dockerfile              # Multi-stage: build frontend + server image
├── container/
│   ├── Dockerfile          # Sandbox: Ubuntu 24.04 + Node 22 + Claude Code + git + gh + tmux
│   └── entrypoint.sh       # GitHub auth, repo clone, exec bash
├── server/
│   └── server.py           # Everything: sessions, WebSocket bridge, cleanup, static serving
├── app/
│   ├── package.json
│   ├── vite.config.ts      # Dev proxy to server
│   └── src/
│       ├── App.tsx
│       ├── styles.css                  # Tokyo Night theme
│       ├── contexts/SessionContext.tsx  # REST API, localStorage persistence
│       ├── hooks/useTerminal.ts        # xterm.js + WebSocket + auto-reconnect
│       └── components/
│           ├── SetupForm.tsx           # Credentials form
│           └── TerminalView.tsx        # Full-screen terminal
└── k8s/
    ├── namespace.yaml
    ├── rbac.yaml
    └── server.yaml         # Deployment + Service + PVC
```

### API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Server health + active session count |
| `POST` | `/api/sessions` | Create session → `{session_id, ws_url}` |
| `GET` | `/api/sessions/{id}` | Check if session is alive |
| `DELETE` | `/api/sessions/{id}` | Destroy session + container |
| `WS` | `/ws/terminal/{id}` | Terminal (binary TTY + JSON resize control) |
| `GET` | `/*` | SPA static files with fallback to `index.html` |

### WebSocket Protocol

| Direction | Frame | Content |
|-----------|-------|---------|
| Client → Server | Binary | Terminal input (UTF-8 keystrokes) |
| Server → Client | Binary | Terminal output (TTY bytes) |
| Client → Server | Text | `{"type": "resize", "cols": 120, "rows": 30}` |
