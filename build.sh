#!/bin/bash
set -e
cd "$(dirname "$0")"

REGISTRY="ghcr.io/nthh"
TAG="${1:-latest}"

echo "Building remolt-sandbox..."
docker build -t "${REGISTRY}/remolt-sandbox:${TAG}" container/

echo "Building remolt-server..."
docker build -t "${REGISTRY}/remolt-server:${TAG}" .

echo ""
echo "Built:"
echo "  ${REGISTRY}/remolt-sandbox:${TAG}"
echo "  ${REGISTRY}/remolt-server:${TAG}"
echo ""
echo "Push with:"
echo "  docker push ${REGISTRY}/remolt-sandbox:${TAG}"
echo "  docker push ${REGISTRY}/remolt-server:${TAG}"
echo ""
echo "Run locally:"
echo "  docker run -p 3000:8080 -v /var/run/docker.sock:/var/run/docker.sock ${REGISTRY}/remolt-server:${TAG}"
