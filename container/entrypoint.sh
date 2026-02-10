#!/bin/bash
set -e

# Git config
git config --global user.name "${GIT_USER_NAME:-Claude Dev}"
git config --global user.email "${GIT_USER_EMAIL:-dev@remolt.dev}"

# tmux config — enable mouse scrolling and increase scrollback
cat > /home/dev/.tmux.conf << 'TMUX'
set -g mouse on
set -g history-limit 10000
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

## Fixing remolt bugs

If you encounter a bug in the sandbox itself (terminal, auth, UI, server):

1. The source is at `~/remolt-dev/`
2. Fix the issue there
3. Submit a PR: `cd ~/remolt-dev && gh pr create`

See `~/remolt-dev/CLAUDE.md` for full architecture details and conventions.
CLAUDEMD

# Welcome message (shown once per session)
cat >> /home/dev/.bashrc << 'BASHRC'

# Remolt welcome (once per session)
if [ ! -f /tmp/.remolt-welcomed ]; then
    touch /tmp/.remolt-welcomed
    echo ""
    echo -e "\033[1;36m  remolt.dev\033[0m — sandboxed AI coding"
    echo ""
    echo -e "  Run \033[1mclaude\033[0m to start an AI coding session."
    echo -e "  Run \033[1mgh pr create\033[0m to push your work."
    echo ""
    echo -e "  \033[2mHit a bug?"
    echo -e "  The remolt source is at ~/remolt-dev/"
    echo -e "  Ask Claude to fix it and make a PR.\033[0m"
    echo ""
fi
BASHRC

# Keep container alive — interactive shells come via docker exec
exec sleep infinity
