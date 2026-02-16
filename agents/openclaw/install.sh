#!/bin/bash
set -e

# Install OpenClaw
npm install -g openclaw

# Install Homebrew (Linuxbrew) — required by openclaw onboard wizard
# Must install as non-root user; entrypoint runs as dev but Dockerfile.agent
# switches to root for installs, so we need build-essential + run as dev
apt-get update && apt-get install -y build-essential procps file && rm -rf /var/lib/apt/lists/*
su - dev -c 'NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
echo 'eval "$(/home/linuxbrew/.linuxbrew/bin/brew shellenv)"' >> /home/dev/.bashrc
