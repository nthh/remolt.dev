#!/bin/bash
set -e

# Git config
git config --global user.name "${GIT_USER_NAME:-Claude Dev}"
git config --global user.email "${GIT_USER_EMAIL:-dev@remolt.dev}"

# Clone repo if specified
if [ -n "$REPO_URL" ]; then
    git clone "$REPO_URL" /home/dev/workspace 2>/dev/null || echo "Clone failed or repo already exists"
fi

# Keep container alive — interactive shells come via docker exec
echo "Remolt sandbox ready"
exec sleep infinity
