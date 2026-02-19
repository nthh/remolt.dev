# AGENTS.md

Instructions for AI agents working on or deploying this codebase.

## What This Is

remolt.dev provides sandboxed AI coding sessions in the browser. A server (FastAPI) creates sandbox pods (K8s) or containers (Docker), connects a browser terminal via WebSocket, and proxies agent dashboards.

## Architecture

```
Browser (React + xterm.js)
    ↕ WebSocket (binary TTY I/O + JSON control frames)
    ↕ HTTP/WS proxy (agent dashboards like OpenClaw)
Server (FastAPI, single file: server/server.py)
    ↕ Docker API (local) or K8s API (production)
Sandbox Container/Pod (Ubuntu 24.04 + tmux + agent CLI + git + gh)
```

The server auto-detects K8s (checks for `/var/run/secrets/kubernetes.io/serviceaccount`) and falls back to Docker.

## Key Files

| File | What |
|------|------|
| `server/server.py` | Entire backend — sessions, WebSocket, Docker/K8s backends, OAuth, proxy, cleanup |
| `agents/*/agent.json` | Agent plugin configs (image, ports, setup, env, resource limits) |
| `container/Dockerfile.base` | Shared sandbox base image (Ubuntu + Node 22 + git + gh + code-server) |
| `container/Dockerfile.agent` | Per-agent image layer (installs agent CLI on top of base) |
| `container/entrypoint.sh` | Sandbox startup — git config, repo clone, agent setup, code-server |
| `Dockerfile` | Server image (multi-stage: builds frontend, then Python server) |
| `app/src/` | React SPA — xterm.js terminal, session management, auth flow |
| `k8s/` | K8s manifests — namespace, RBAC, server deployment, network policy |

## Conventions

- `server/server.py` is a single file. Don't split it.
- The frontend is a single-page app. The server serves the built SPA from `/app/static`.
- No external databases — session state is in-memory + optional `sessions.json` file.
- Sandbox images should stay minimal — users have `sudo` to install what they need.

## Development

```bash
# Frontend
cd app && npm install && npm run build

# Server deps
pip install fastapi uvicorn[standard] aiodocker httpx websockets cryptography

# Tests
python -m pytest tests/

# Run locally with Docker backend
docker run -p 3000:8080 -v /var/run/docker.sock:/var/run/docker.sock <server-image>
```

---

## Self-Hosting Guide

### Local Only (no cluster, no domain, no internet exposure)

The server auto-detects its environment. Without K8s, it uses Docker directly. This is the fastest way to run remolt locally.

**Prerequisites:** Docker running on your machine.

```bash
git clone https://github.com/nthh/remolt.dev.git && cd remolt.dev

# Build the sandbox image (used for all agents by default)
docker build -t remolt-sandbox -f container/Dockerfile.base container/

# Build the server image
docker build -t remolt-server .

# Run — mounts Docker socket so the server can create sandbox containers
docker run -p 3000:8080 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e REMOLT_SANDBOX_IMAGE=remolt-sandbox \
  -e REMOLT_ALLOWED_ORIGINS=http://localhost:3000 \
  remolt-server
```

Open http://localhost:3000. Auth is disabled (no `GITHUB_CLIENT_ID` set), so you go straight to the setup form. Sandboxes run as Docker containers on your machine.

To build a per-agent image (e.g. with Claude Code pre-installed):

```bash
docker build \
  --build-arg BASE_IMAGE=remolt-sandbox \
  --build-arg AGENT_INSTALL="npm install -g @anthropic-ai/claude-code" \
  -t remolt-claude-code \
  -f container/Dockerfile.agent container/

# Then run the server with the agent image override:
docker run -p 3000:8080 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e REMOLT_SANDBOX_IMAGE=remolt-sandbox \
  -e REMOLT_CLAUDE_CODE_IMAGE=remolt-claude-code \
  -e REMOLT_ALLOWED_ORIGINS=http://localhost:3000 \
  remolt-server
```

**Notes for local mode:**
- No HTTPS needed — auth cookies aren't set when auth is disabled, and localhost is fine over HTTP.
- `git push` / `gh` won't work in sandboxes unless users run `gh auth login` manually (no GitHub token injected without OAuth).
- The warm pool (`REMOLT_WARM_POOL`) works with Docker too but is unnecessary for single-user local use — set it to `0` or leave it unset.
- Sandbox containers are cleaned up when sessions are destroyed or idle-timeout. If the server crashes, orphaned containers with label `remolt.managed=true` need manual cleanup: `docker ps -f label=remolt.managed=true -q | xargs docker rm -f`.

---

### Kubernetes (private, no ingress)

Run on a K8s cluster but access it via `kubectl port-forward` — no domain, no TLS, no ingress controller needed. Good for personal use or trying it out on an existing cluster.

```bash
git clone https://github.com/nthh/remolt.dev.git && cd remolt.dev

REGISTRY=your-registry.example.com

# Build and push images (cluster nodes need to pull these)
docker buildx build --platform linux/amd64 \
  -t $REGISTRY/remolt-sandbox-base:latest \
  -f container/Dockerfile.base container/ --push

docker buildx build --platform linux/amd64 \
  --build-arg BASE_IMAGE=$REGISTRY/remolt-sandbox-base:latest \
  --build-arg AGENT_INSTALL="npm install -g @anthropic-ai/claude-code" \
  -t $REGISTRY/remolt-claude-code:latest \
  -f container/Dockerfile.agent container/ --push

docker buildx build --platform linux/amd64 \
  -t $REGISTRY/remolt-server:latest --push .
```

Edit `k8s/server.yaml` — change the image references and set the origin to your port-forward address:

```yaml
image: your-registry.example.com/remolt-server:latest
# ...
- name: REMOLT_SANDBOX_IMAGE
  value: "your-registry.example.com/remolt-sandbox-base:latest"
- name: REMOLT_CLAUDE_CODE_IMAGE
  value: "your-registry.example.com/remolt-claude-code:latest"
- name: REMOLT_ALLOWED_ORIGINS
  value: "http://localhost:3000"
```

Apply and port-forward:

```bash
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/rbac.yaml
kubectl apply -f k8s/server.yaml
# Network policy is optional for private use but still recommended:
kubectl apply -f k8s/network-policy.yaml

kubectl -n remolt port-forward svc/remolt-server 3000:8080
```

Open http://localhost:3000. No auth, no TLS — same as local Docker mode but sandboxes run as K8s pods with proper resource limits and isolation.

---

### Kubernetes (production, exposed)

For running on a cluster with multiple users, domains, and TLS.

You need: a Linux server (4+ CPU, 8+ GB RAM), a domain, and a container registry.

#### 1. Set up a cluster

The simplest option is single-node k3s:

```bash
curl -sfL https://get.k3s.io | sh -
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
```

If you need NetworkPolicy enforcement (recommended — isolates sandboxes from cluster internals), install Calico:

```bash
kubectl apply -f https://raw.githubusercontent.com/projectcalico/calico/v3.27.0/manifests/calico.yaml
```

Any managed K8s cluster (GKE, EKS, AKS) works too.

#### 2. Build and push images

```bash
git clone https://github.com/nthh/remolt.dev.git && cd remolt.dev

REGISTRY=your-registry.example.com   # e.g. ghcr.io/yourname, docker.io/yourname

# Base sandbox image
docker buildx build --platform linux/amd64 \
  -t $REGISTRY/remolt-sandbox-base:latest \
  -f container/Dockerfile.base container/ --push

# Claude Code sandbox image
docker buildx build --platform linux/amd64 \
  --build-arg BASE_IMAGE=$REGISTRY/remolt-sandbox-base:latest \
  --build-arg AGENT_INSTALL="npm install -g @anthropic-ai/claude-code" \
  -t $REGISTRY/remolt-claude-code:latest \
  -f container/Dockerfile.agent container/ --push

# Server image
docker buildx build --platform linux/amd64 \
  -t $REGISTRY/remolt-server:latest --push .
```

For OpenClaw, check `agents/openclaw/` for an `install.sh` that handles its more complex setup (Homebrew, etc.).

#### 3. Apply K8s manifests

```bash
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/rbac.yaml
```

#### 4. Configure the server deployment

Edit `k8s/server.yaml` before applying. Change these values:

| Field / Env var | Change to |
|-----------------|-----------|
| `image:` (container spec) | `$REGISTRY/remolt-server:latest` |
| `REMOLT_SANDBOX_IMAGE` | `$REGISTRY/remolt-sandbox-base:latest` |
| `REMOLT_ALLOWED_ORIGINS` | `https://your-domain.com` |

Add per-agent image overrides:

```yaml
- name: REMOLT_CLAUDE_CODE_IMAGE
  value: "$REGISTRY/remolt-claude-code:latest"
```

The pattern for any agent is `REMOLT_{AGENT_ID_UPPER_WITH_UNDERSCORES}_IMAGE`.

```bash
kubectl apply -f k8s/server.yaml
kubectl apply -f k8s/network-policy.yaml
```

#### 5. Authentication (optional)

**GitHub OAuth is optional.** If you don't configure it, auth is disabled — everyone runs as "anonymous" and the login screen is skipped. This is fine for personal/internal use.

To skip auth: don't create the `remolt-auth` secret. That's it — the server checks `bool(GITHUB_CLIENT_ID)` and disables all auth when it's empty.

Without GitHub OAuth, sandboxes won't have a `GITHUB_TOKEN` injected, so `git push` and `gh` won't work unless the user runs `gh auth login` manually inside the sandbox.

**To enable GitHub OAuth:**

1. Create an OAuth App at https://github.com/settings/developers
   - Homepage URL: `https://your-domain.com`
   - Callback URL: `https://your-domain.com/auth/callback`
2. Create the secret:

```bash
kubectl -n remolt create secret generic remolt-auth \
  --from-literal=GITHUB_CLIENT_ID=<client-id> \
  --from-literal=GITHUB_CLIENT_SECRET=<client-secret> \
  --from-literal=COOKIE_SECRET=$(openssl rand -hex 32)
```

If `COOKIE_SECRET` is not set, a random one is generated at startup (auth cookies invalidate on every server restart).

#### 6. Expose the server

The server listens on port 8080. It needs HTTPS (auth cookies are `secure=True`) and WebSocket support.

**Option A: Cloudflare Tunnel**

Edit `k8s/cloudflared.yaml` with your tunnel UUID and domain, create the credentials secret, and apply:

```bash
kubectl -n remolt create secret generic cloudflared-creds \
  --from-file=credentials.json=<path-to-tunnel-credentials>
kubectl apply -f k8s/cloudflared.yaml
```

**Option B: Ingress controller (nginx, Traefik, etc.)**

k3s ships with Traefik. Create an Ingress resource pointing to `remolt-server:8080`. Make sure WebSocket upgrade is supported and timeouts are long enough (sandbox sessions last up to 4 hours):

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: remolt
  namespace: remolt
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt
    nginx.ingress.kubernetes.io/proxy-read-timeout: "3600"
    nginx.ingress.kubernetes.io/proxy-send-timeout: "3600"
spec:
  ingressClassName: nginx
  tls:
    - hosts: [your-domain.com]
      secretName: remolt-tls
  rules:
    - host: your-domain.com
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: remolt-server
                port:
                  number: 8080
```

#### 7. Verify

```bash
kubectl -n remolt get pods
kubectl -n remolt logs -l app=remolt-server --tail=20
curl https://your-domain.com/health
# Should return: {"status":"ok","version":"..."}
```

### Environment Variables Reference

All env vars on the server pod:

| Variable | Default | Description |
|----------|---------|-------------|
| `GITHUB_CLIENT_ID` | `""` | GitHub OAuth client ID. Empty = auth disabled |
| `GITHUB_CLIENT_SECRET` | `""` | GitHub OAuth client secret |
| `COOKIE_SECRET` | random | Fernet key for auth cookies. Set explicitly for stable sessions across restarts |
| `REMOLT_SANDBOX_IMAGE` | `"remolt-sandbox"` | Fallback sandbox image if no per-agent override |
| `REMOLT_MAX_SESSIONS` | `10` | Max concurrent sessions globally |
| `REMOLT_MAX_USER_SESSIONS` | `2` | Max sessions per user |
| `REMOLT_MAX_IDLE_SECONDS` | `3600` | Idle timeout before session is destroyed |
| `REMOLT_WARM_POOL` | `0` | Pre-warmed pods per eligible agent (set to 0 to disable) |
| `REMOLT_SANDBOX_BANDWIDTH` | `"100mbit"` | Per-pod egress bandwidth cap via tc |
| `REMOLT_NAMESPACE` | `"remolt"` | K8s namespace for sandbox pods |
| `REMOLT_ALLOWED_ORIGINS` | `"http://localhost:5173"` | Comma-separated CORS origins |
| `REMOLT_SESSIONS_FILE` | `""` | Path for session persistence JSON (enables recovery after restarts) |
| `REMOLT_EVENTS_LOG` | `""` | Path for analytics JSONL |

### Gotchas

| Issue | Fix |
|-------|-----|
| **Sandbox pods won't start** | The bandwidth-limiting init container needs `NET_ADMIN` capability. If your cluster's PodSecurityStandards block it, either allow it for the `remolt` namespace or the init container needs to be removed from `server.py`. |
| **NetworkPolicy not enforced** | Your CNI must support it (Calico, Cilium). Flannel alone doesn't enforce NetworkPolicy — sandboxes could reach other cluster services. |
| **Private container registry** | Add `imagePullSecrets` to the server deployment and to the sandbox pod spec in `server.py`. |
| **OpenClaw domain hardcoded** | `agents/openclaw/agent.json` has `allowedOrigins: ["https://remolt.dev"]` in its setup script — change it to your domain before building. |
| **Session loss on restart** | Set `REMOLT_SESSIONS_FILE` to a path on a PersistentVolume. Without it, the server loses track of running pods on restart and destroys them. |
| **Single replica only** | Session state is in-memory. Multiple server replicas would split sessions. |
