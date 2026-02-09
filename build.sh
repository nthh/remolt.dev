#!/bin/bash
set -e
cd "$(dirname "$0")"

REGISTRY="ghcr.io/nthh"
TAG="${1:-latest}"
PLATFORM="linux/amd64"

echo "Building remolt-sandbox (${PLATFORM})..."
docker buildx build --platform "${PLATFORM}" -t "${REGISTRY}/remolt-sandbox:${TAG}" --push container/

echo "Building remolt-server (${PLATFORM})..."
docker buildx build --platform "${PLATFORM}" -t "${REGISTRY}/remolt-server:${TAG}" --push .

echo ""
echo "Pushed:"
echo "  ${REGISTRY}/remolt-sandbox:${TAG}"
echo "  ${REGISTRY}/remolt-server:${TAG}"
echo ""
echo "Run locally:"
echo "  docker run -p 3000:8080 -v /var/run/docker.sock:/var/run/docker.sock ${REGISTRY}/remolt-server:${TAG}"
