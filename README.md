# [remolt.dev](https://remolt.dev)

Sandboxed AI coding sessions in your browser. Choose an AI agent — Claude Code, OpenClaw, or others — launch a full Linux terminal with it, authenticate interactively, push commits directly from the session.

**License:** [MIT](LICENSE)

---

## How It Works

```
Browser (React + xterm.js)
    ↕ WebSocket (binary TTY I/O + JSON control frames)
    ↕ HTTP/WS proxy (agent dashboards like OpenClaw)
Server (FastAPI, single file, serves SPA + API)
    ↕ Docker API (local) or K8s API (production)
Sandbox Container/Pod (Ubuntu 24.04 + tmux + agent CLI + git + gh)
```

1. User signs in with GitHub (OAuth — optional for local dev, required in production)
2. User selects an agent (Claude Code, OpenClaw, etc.)
3. Server creates a sandbox with the agent's container image
4. GitHub token + agent-specific env vars injected into sandbox
5. Browser connects via WebSocket to a tmux session inside the sandbox
6. For dashboard-based agents (OpenClaw), server proxies HTTP + WebSocket to the agent's port
7. For terminal agents (Claude Code), user runs `claude` — auth URL auto-detected and shown as banner
8. Session auto-destroyed after 1 hour idle (configurable)

**Runtime auto-detection:** The server checks for a K8s service account at startup. If found, it creates Pods via the K8s API. Otherwise, it uses the Docker API. Same code, no configuration needed.

**No API key needed.** Claude Code supports interactive OAuth — run `claude` in the terminal, and the auth URL is automatically detected and shown as a clickable banner above the terminal.

---

## Sessions

### Lifecycle

```
Launch Session
  → Container created with user's env vars
  → Entrypoint: git config → repo clone → bash
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

**Session persistence** — The server writes session metadata to disk (`sessions.json`). On restart, it scans for sandboxes labeled `remolt.managed=true` (Docker containers or K8s Pods), reconciles with the saved metadata, and reclaims running sessions.

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

**The server never stores credentials on disk.** OAuth tokens live inside the container and are destroyed with it.

### Browser-side

| Data | Where | Lifetime |
|------|-------|----------|
| Session ID | `localStorage` (`remolt:session`) | Until session ends |
| Preferences | `localStorage` (`remolt:prefs`) | Indefinite |

Preferences include git name, git email, and selected agent — convenience fields so you don't re-type them.

### Analytics Events

Structured JSON lines written to stdout (captured by Docker logs) and optionally to a file:

```jsonl
{"ts": 1739120400.0, "event": "server.started", "max_sessions": 10, "max_idle_s": 3600}
{"ts": 1739120410.5, "event": "session.created", "session_id": "a1b2c3", "has_repo": true}
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

## Agents

Remolt supports multiple AI coding agents via a plugin system. Each agent is defined by a JSON config at `agents/{id}/agent.json`.

| Agent | Type | Description |
|-------|------|-------------|
| **Claude Code** | Terminal | Anthropic's AI coding CLI — runs in tmux |
| **OpenClaw** | Dashboard | Autonomous AI agent with web UI — proxied at `/proxy/{session_id}/` |

### Adding a New Agent

Create `agents/{id}/agent.json`:

```json
{
  "id": "my-agent",
  "name": "My Agent",
  "description": "What it does",
  "install": "npm install -g my-agent",
  "setup": "my-agent start --port 9000 &",
  "ports": [{ "port": 9000, "label": "Dashboard" }],
  "warm_pool": false
}
```

The build system auto-discovers agents and creates per-agent container images (`remolt-{id}`).

## Sandbox Environment

Each sandbox is a full Ubuntu 24.04 environment. Pre-installed in the base image:

| Tool | What |
|------|------|
| `git` | Version control |
| `gh` | [GitHub CLI](https://cli.github.com/) — push, PR, issues |
| `node` / `npm` | Node.js 22 LTS |
| `tmux` | Terminal multiplexer (session persistence) |
| `sudo` | Install anything else |

The selected agent CLI is installed on top of the base image. Since you have `sudo`, install whatever else you need:

```bash
# Python
sudo apt-get update && sudo apt-get install -y python3 python3-pip

# Rust
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
```

### GitHub Integration

When GitHub OAuth is configured, your token is automatically injected into the sandbox as `GITHUB_TOKEN`. This means:

- `git push` works immediately — no manual auth needed
- `gh` CLI works out of the box — create PRs, browse issues, manage releases
- Private repos accessible — clone any repo you have access to inside the sandbox

Without OAuth (local dev), run `gh auth login` in the terminal to authenticate via browser device flow.

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
| `REMOLT_WARM_POOL` | `0` | Number of pre-warmed sandboxes to keep ready |
| `REMOLT_NAMESPACE` | `remolt` | K8s namespace for sandbox Pods (K8s backend only) |
| `REMOLT_STATIC_DIR` | _(none)_ | Path to built frontend (`/app/static` in Docker image) |
| `GITHUB_CLIENT_ID` | _(none)_ | GitHub OAuth App client ID (enables auth gate) |
| `GITHUB_CLIENT_SECRET` | _(none)_ | GitHub OAuth App client secret |
| `COOKIE_SECRET` | _(random)_ | HMAC key for signing auth cookies (generate with `openssl rand -hex 32`) |

When `GITHUB_CLIENT_ID` is set, users must sign in with GitHub before creating sessions. The OAuth token is injected into sandboxes as `GITHUB_TOKEN`. Without it, auth is disabled and the app works as before.

**Setup:** Copy `.env.example` to `.env`, fill in values from your [GitHub OAuth App](https://github.com/settings/developers) (callback URL: `https://your-domain/auth/callback`). The server loads `.env` automatically. For K8s, create a secret: `kubectl -n remolt create secret generic remolt-auth --from-env-file=.env`.

---

## Development

For local iteration without Docker rebuilds:

```bash
# Terminal 1: server (needs Docker running for sandbox containers)
cd server
pip install fastapi uvicorn[standard] aiodocker httpx websockets
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
| Server + SPA | Vultr K8s, `remolt` namespace |
| Sandbox containers | Vultr K8s Pods |
| Ingress | Cloudflare Tunnel (cloudflared) |
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

- Each session is an isolated container/pod with no shared filesystem
- **Docker:** Each container gets its own bridge network (`remolt-net-{id}`), preventing container-to-container traffic
- **K8s:** NetworkPolicy denies pod-to-pod traffic between sandbox pods
- Containers run as non-root user `dev` (with sudo for convenience)
- No access to host network, other containers, or server filesystem
- Server communicates with sandboxes only via exec API (Docker exec or K8s exec)

### Credentials

- OAuth tokens live inside the container — never written to server disk
- GitHub auth state lives inside the container (via `gh auth login`)
- All credentials destroyed when the container is removed
- Browser stores only session ID and non-secret preferences

### Threat Model

Users can run arbitrary code — that's the point. Abuse is mitigated by:

- **Session limits** — `REMOLT_MAX_SESSIONS` caps concurrent sandboxes
- **Resource limits** — K8s CPU/memory limits per pod (2 CPU, 2Gi default)
- **Idle timeout** — sandboxes auto-destroyed after inactivity
- **Isolation** — each sandbox is a separate container/pod with no shared state

---

## Architecture

```
remolt-dev/
├── README.md
├── Dockerfile              # Multi-stage: build frontend + server image
├── agents/
│   ├── claude-code/
│   │   └── agent.json      # Claude Code agent config
│   └── openclaw/
│       └── agent.json      # OpenClaw agent config (ports, setup, env)
├── container/
│   ├── Dockerfile.base     # Shared base: Ubuntu 24.04 + Node 22 + git + gh + tmux
│   ├── Dockerfile.agent    # Per-agent layer: installs agent CLI
│   └── entrypoint.sh       # Git config, repo clone, agent setup
├── server/
│   └── server.py           # Everything: sessions, WebSocket bridge, agent proxy, cleanup
├── app/
│   ├── package.json
│   ├── vite.config.ts      # Dev proxy to server
│   └── src/
│       ├── App.tsx
│       ├── styles.css                  # Tokyo Night theme
│       ├── contexts/SessionContext.tsx  # REST API, agent list, localStorage
│       ├── hooks/useTerminal.ts        # xterm.js + WebSocket + auto-reconnect
│       └── components/
│           ├── SetupForm.tsx           # Agent selector + API keys form
│           ├── WorkspaceView.tsx       # Tab management, upload/download
│           ├── TabBar.tsx              # Terminal tabs + action buttons
│           ├── TerminalPanel.tsx       # Terminal instance + toolbar
│           ├── DashboardPanel.tsx      # Agent dashboard iframe
│           ├── LogsPanel.tsx           # Agent log streaming
│           ├── VSCodePanel.tsx         # VS Code (code-server) iframe
│           └── SettingsModal.tsx       # API key management modal
└── k8s/
    ├── namespace.yaml
    ├── network-policy.yaml # Deny sandbox-to-sandbox traffic
    ├── rbac.yaml
    └── server.yaml         # Deployment + Service + PVC
```

### API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Server health + active session count |
| `GET` | `/auth/login` | Redirect to GitHub OAuth |
| `GET` | `/auth/callback` | OAuth callback (sets auth cookie) |
| `GET` | `/auth/me` | Current user info (or 401) |
| `GET` | `/auth/logout` | Clear auth cookie |
| `GET` | `/api/agents` | List available agents |
| `GET` | `/api/keys` | List stored API key names |
| `PUT` | `/api/keys` | Store API keys (encrypted, per-user) |
| `DELETE` | `/api/keys/{name}` | Delete a stored API key |
| `POST` | `/api/sessions` | Create session → `{session_id, ws_url, agent_type, proxy_url}` |
| `GET` | `/api/sessions/{id}` | Check if session is alive |
| `DELETE` | `/api/sessions/{id}` | Destroy session + container |
| `GET` | `/api/sessions/{id}/download` | Download workspace as `.tar.gz` |
| `POST` | `/api/sessions/{id}/upload` | Upload `.tar.gz` to restore workspace |
| `WS` | `/ws/terminal/{id}` | Terminal (binary TTY + JSON resize control) |
| `WS` | `/ws/logs/{id}` | Streaming agent logs |
| `WS` | `/vscode/{id}/{path}` | VS Code (code-server) proxy |
| `GET/WS` | `/proxy/{id}/{path}` | HTTP + WebSocket proxy to agent dashboard |
| `GET` | `/*` | SPA static files with fallback to `index.html` |

### WebSocket Protocol

| Direction | Frame | Content |
|-----------|-------|---------|
| Client → Server | Binary | Terminal input (UTF-8 keystrokes) |
| Server → Client | Binary | Terminal output (TTY bytes) |
| Client → Server | Text | `{"type": "resize", "cols": 120, "rows": 30}` |
