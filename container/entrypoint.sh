#!/bin/bash
set -e

# Git config
git config --global user.name "${GIT_USER_NAME:-Claude Dev}"
git config --global user.email "${GIT_USER_EMAIL:-dev@remolt.dev}"

# Pre-configure Claude Code (skip onboarding prompts)
mkdir -p /home/dev/.claude
cat > /home/dev/.claude/settings.json << 'SETTINGS'
{
  "permissions": {
    "allow": [],
    "deny": []
  },
  "hasCompletedOnboarding": true
}
SETTINGS

# Clone user's repo if specified
if [ -n "$REPO_URL" ]; then
    git clone "$REPO_URL" /home/dev/workspace 2>/dev/null || echo "Clone failed or repo already exists"
fi

# Clone remolt source so users can fix bugs and contribute
git clone https://github.com/nthh/remolt.dev.git /home/dev/remolt-dev 2>/dev/null || true

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
    echo -e "  \033[2mHit a bug? The remolt source is at ~/remolt-dev/"
    echo -e "  Ask Claude to fix it and make a PR.\033[0m"
    echo ""
fi
BASHRC

# Keep container alive — interactive shells come via docker exec
exec sleep infinity
