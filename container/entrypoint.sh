#!/bin/bash
set -e

# Git config
git config --global user.name "${GIT_USER_NAME:-Claude Dev}"
git config --global user.email "${GIT_USER_EMAIL:-dev@remolt.dev}"

# tmux config — enable mouse scrolling and increase scrollback
cat > /home/dev/.tmux.conf << 'TMUX'
set -g mouse on
set -g history-limit 10000
set -g status off
bind-key PPage copy-mode -eu
TMUX

# Pre-configure Claude Code (theme only — don't set hasCompletedOnboarding, it skips auth)
mkdir -p /home/dev/.claude
echo '{"theme":"dark"}' > /home/dev/.claude.json
cat > /home/dev/.claude/settings.json << 'SETTINGS'
{
  "permissions": {
    "allow": [],
    "deny": []
  }
}
SETTINGS

# Clone user's repo if specified
if [ -n "$REPO_URL" ]; then
    git clone "$REPO_URL" /home/dev/workspace 2>/dev/null || echo "Clone failed or repo already exists"
fi

# Clone remolt source so users can fix bugs and contribute
git clone https://github.com/nthh/remolt.dev.git /home/dev/remolt-dev 2>/dev/null || true

# Give Claude Code context about this sandbox
cat > /home/dev/CLAUDE.md << 'CLAUDEMD'
# You are inside a remolt.dev sandbox

This is a cloud sandbox running Ubuntu 24.04 with tmux, git, gh, and Claude Code pre-installed.

## Key facts

- **GitHub token** is pre-configured — `gh` and `git push` work immediately
- **Remolt source** is at `~/remolt-dev/` — you can fix bugs and submit PRs
- **User's repo** (if any) is at `~/workspace/`

## Fixing bugs & requesting features

If you encounter a bug or have a feature request for remolt:

1. The source is at `~/remolt-dev/`
2. For bugs: fix the issue and submit a PR: `cd ~/remolt-dev && gh pr create`
3. For feature requests: file an issue: `cd ~/remolt-dev && gh issue create`
4. Fork first if you don't have push access: `cd ~/remolt-dev && gh repo fork --remote`

See `~/remolt-dev/CLAUDE.md` for full architecture details and conventions.
CLAUDEMD

# Ensure workspace directory exists (code-server errors if it doesn't)
mkdir -p /home/dev/workspace

# Start code-server (VS Code in browser) on port 18080
code-server --bind-addr 0.0.0.0:18080 --auth none --disable-telemetry /home/dev/workspace &

# Run agent setup command if provided (e.g., start OpenClaw gateway)
if [ -n "$AGENT_SETUP" ]; then
    eval "$AGENT_SETUP" || echo "Warning: agent setup command failed"
fi

# Welcome message — use AGENT_WELCOME if set, otherwise default
WELCOME_MSG="${AGENT_WELCOME:-Run \`claude\` to start an AI coding session.\nRun \`gh pr create\` to push your work.}"

cat >> /home/dev/.bashrc << BASHRC

# Remolt welcome (once per session)
if [ ! -f /tmp/.remolt-welcomed ]; then
    touch /tmp/.remolt-welcomed
    echo ""
    echo -e "\033[1;36m  remolt.dev\033[0m — sandboxed AI coding"
    echo ""
    echo -e "  ${WELCOME_MSG}"
    echo ""
    echo -e "  \033[2mHit a bug or have a feature request?"
    echo -e "  The remolt source is at ~/remolt-dev/"
    echo -e "  Ask Claude to fix it and make a PR,"
    echo -e "  or file an issue with gh issue create.\033[0m"
    echo ""
fi
BASHRC

# Keep container alive — interactive shells come via docker exec
exec sleep infinity
