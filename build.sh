#!/bin/bash
set -e
cd "$(dirname "$0")"
docker build -t remolt-sandbox container/
docker build -t remolt .
echo ""
echo "Done. Run with:"
echo "  docker run -p 3000:8080 -v /var/run/docker.sock:/var/run/docker.sock remolt"
