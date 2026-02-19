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

Pick the setup that matches your situation:

| Setup | When to use | What you need |
|-------|-------------|---------------|
| **[Local (k3s)](#local-k3s-no-domain-no-internet-exposure)** | Personal use, trying it out, no internet exposure needed | Linux machine (or VM) with Docker |
| **[Local (Docker only)](#local-k3s-no-domain-no-internet-exposure)** | Quickest possible start, don't need pod isolation | Docker on any OS |
| **[K8s (production)](#kubernetes-production-exposed)** | Multi-user, public-facing, TLS + auth | K8s cluster, domain, container registry |

The recommended path is **local k3s** — it's almost as easy as raw Docker but gives you the real K8s environment with pod isolation, resource limits, and network policies.

### Local (k3s, no domain, no internet exposure)

The recommended way to run remolt locally. k3s gives you a real K8s environment with pod isolation, resource limits, and network policies — and it's one command to install. No domain or TLS needed.

**Prerequisites:** Linux machine (or VM) with Docker.

#### 1. Install k3s

```bash
curl -sfL https://get.k3s.io | sh -
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
```

k3s includes a container runtime (containerd) and Traefik, but you don't need Traefik for local use.

#### 2. Build and import images

Since you're running locally, you can import images directly into k3s instead of pushing to a registry. Use a tag other than `:latest` (e.g. `:local`) — K8s defaults to `imagePullPolicy: Always` for `:latest`, which tries to contact a registry and fails. Non-latest tags default to `IfNotPresent`, which uses the locally imported image.

```bash
git clone https://github.com/nthh/remolt.dev.git && cd remolt.dev

# Build images locally
docker build -t remolt-sandbox-base:local -f container/Dockerfile.base container/
docker build \
  --build-arg BASE_IMAGE=remolt-sandbox-base:local \
  --build-arg AGENT_INSTALL="npm install -g @anthropic-ai/claude-code" \
  -t remolt-claude-code:local \
  -f container/Dockerfile.agent container/
docker build -t remolt-server:local .

# Import into k3s (no registry needed)
docker save remolt-sandbox-base:local | sudo k3s ctr images import -
docker save remolt-claude-code:local | sudo k3s ctr images import -
docker save remolt-server:local | sudo k3s ctr images import -
```

#### 3. Configure and apply manifests

Edit `k8s/server.yaml` — change image references to your local tags and set `imagePullPolicy: IfNotPresent`:

```yaml
image: remolt-server:local
imagePullPolicy: IfNotPresent
# ...
- name: REMOLT_SANDBOX_IMAGE
  value: "remolt-sandbox-base:local"
- name: REMOLT_CLAUDE_CODE_IMAGE
  value: "remolt-claude-code:local"
- name: REMOLT_ALLOWED_ORIGINS
  value: "http://localhost:3000"
- name: REMOLT_WARM_POOL
  value: "0"
```

Note: the sandbox pod spec in `server.py` does not set `imagePullPolicy`, so K8s uses its default. With non-`:latest` tags this defaults to `IfNotPresent`, which uses the imported images. If you need to force it, add `"imagePullPolicy": "IfNotPresent"` to the container spec in the `K8sBackend.create()` method.

```bash
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/rbac.yaml
kubectl apply -f k8s/server.yaml
kubectl apply -f k8s/network-policy.yaml   # optional for local, but recommended
```

#### 4. Access it

```bash
kubectl -n remolt port-forward svc/remolt-server 3000:8080
```

Open http://localhost:3000. Auth is disabled (no `GITHUB_CLIENT_ID`), so you go straight to the setup form.

**Notes:**
- No HTTPS needed — auth cookies aren't set when auth is disabled.
- `git push` / `gh` won't work in sandboxes unless users run `gh auth login` manually (no GitHub token injected without OAuth).
- NetworkPolicy enforcement requires a CNI that supports it. k3s uses Flannel by default which doesn't enforce. For local use this is fine. For production, install Calico: `kubectl apply -f https://raw.githubusercontent.com/projectcalico/calico/v3.27.0/manifests/calico.yaml`

> **Alternative: raw Docker (no K8s).** The server falls back to Docker when not in K8s — `docker run -p 3000:8080 -v /var/run/docker.sock:/var/run/docker.sock -e REMOLT_ALLOWED_ORIGINS=http://localhost:3000 remolt-server`. Simpler but no pod isolation, resource limits, or network policies.

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
