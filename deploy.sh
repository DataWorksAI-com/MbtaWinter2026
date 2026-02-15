#!/usr/bin/env bash
# ============================================================
# deploy.sh — Build, push, and deploy MBTA Winter 2026 to LKE
#
# Prerequisites:
#   - Docker installed and running
#   - kubectl configured (via terraform/kubeconfig.yaml)
#   - Container registry accessible (Docker Hub, Harbor, etc.)
#
# Usage:
#   export DOCKER_REGISTRY=your-registry.example.com/mbta
#   bash deploy.sh [build|push|apply|all]
#
# Build settings (optional):
#   USE_BUILDX=true                # default: true
#   BUILD_PLATFORMS=linux/amd64,linux/arm64
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKER_REGISTRY="${DOCKER_REGISTRY:?Error: Set DOCKER_REGISTRY env var (e.g. docker.io/youruser)}"
TAG="${TAG:-latest}"
USE_BUILDX="${USE_BUILDX:-true}"
BUILD_PLATFORMS="${BUILD_PLATFORMS:-linux/amd64,linux/arm64}"

# Colors
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

get_dockerhub_creds() {
  if [[ -n "${DOCKERHUB_USERNAME:-}" && -n "${DOCKERHUB_TOKEN:-}" ]]; then
    return 0
  fi

  local docker_config
  docker_config="${HOME}/.docker/config.json"
  if [[ ! -f "${docker_config}" ]]; then
    return 1
  fi

  local auth
  auth=$(python3 - << 'PY'
import json
import os

path = os.path.expanduser("~/.docker/config.json")
try:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
except Exception:
    print("")
    raise SystemExit(0)

auths = data.get("auths", {})
for key in ("https://index.docker.io/v1/", "docker.io"):
    if key in auths and "auth" in auths[key]:
        print(auths[key]["auth"])
        raise SystemExit(0)
print("")
PY
)

  if [[ -z "${auth}" ]]; then
    return 1
  fi

  local decoded
  decoded=$(printf "%s" "${auth}" | base64 -D 2>/dev/null || printf "%s" "${auth}" | base64 -d 2>/dev/null || true)
  if [[ "${decoded}" != *:* ]]; then
    return 1
  fi

  DOCKERHUB_USERNAME="${decoded%%:*}"
  DOCKERHUB_TOKEN="${decoded#*:}"
  export DOCKERHUB_USERNAME DOCKERHUB_TOKEN
  return 0
}

# ─────────────────────────────────────────────
# Build Docker images
# ─────────────────────────────────────────────
build() {
  info "Building Docker images..."

  if [[ "${USE_BUILDX}" == "true" ]]; then
    info "Using buildx (${BUILD_PLATFORMS}) and pushing images..."
    docker buildx create --use --name mbta-builder >/dev/null 2>&1 || docker buildx use mbta-builder

    info "Building exchange image (multi-arch)..."
    docker buildx build --platform "${BUILD_PLATFORMS}" \
      -t "${DOCKER_REGISTRY}/mbta-exchange:${TAG}" \
      -f docker/Dockerfile.exchange . --push

    info "Building agent image (multi-arch)..."
    docker buildx build --platform "${BUILD_PLATFORMS}" \
      -t "${DOCKER_REGISTRY}/mbta-agent:${TAG}" \
      -f docker/Dockerfile.agent . --push

    info "Building registry image (multi-arch)..."
    docker buildx build --platform "${BUILD_PLATFORMS}" \
      -t "${DOCKER_REGISTRY}/mbta-registry:${TAG}" \
      -f docker/Dockerfile.registry . --push

    info "All images built and pushed successfully!"
  else
    info "Building exchange image..."
    docker build -t "${DOCKER_REGISTRY}/mbta-exchange:${TAG}" \
      -f docker/Dockerfile.exchange .

    info "Building agent image..."
    docker build -t "${DOCKER_REGISTRY}/mbta-agent:${TAG}" \
      -f docker/Dockerfile.agent .

    info "Building registry image..."
    docker build -t "${DOCKER_REGISTRY}/mbta-registry:${TAG}" \
      -f docker/Dockerfile.registry .

    info "All images built successfully!"
  fi
}

# ─────────────────────────────────────────────
# Push Docker images to registry
# ─────────────────────────────────────────────
push() {
  if [[ "${USE_BUILDX}" == "true" ]]; then
    warn "USE_BUILDX=true: images are already pushed during build."
    return 0
  fi

  info "Pushing Docker images to ${DOCKER_REGISTRY}..."
  docker push "${DOCKER_REGISTRY}/mbta-exchange:${TAG}"
  docker push "${DOCKER_REGISTRY}/mbta-agent:${TAG}"
  docker push "${DOCKER_REGISTRY}/mbta-registry:${TAG}"
  info "All images pushed!"
}

# ─────────────────────────────────────────────
# Apply Kubernetes manifests
# ─────────────────────────────────────────────
apply() {
  info "Deploying to Kubernetes..."

  # Check kubeconfig
  if [[ -f "${SCRIPT_DIR}/terraform/kubeconfig.yaml" ]]; then
    export KUBECONFIG="${SCRIPT_DIR}/terraform/kubeconfig.yaml"
    info "Using kubeconfig from terraform/"
  fi

  # Check secrets exist
  if [[ ! -f "${SCRIPT_DIR}/k8s/secrets.yaml" ]]; then
    warn "k8s/secrets.yaml not found!"
    warn "Copy k8s/secrets.example.yaml to k8s/secrets.yaml and fill in your API keys."
    error "Cannot deploy without secrets."
  fi

  # Apply non-templated manifests
  kubectl apply -f "${SCRIPT_DIR}/k8s/namespace.yaml"
  kubectl apply -f "${SCRIPT_DIR}/k8s/configmap.yaml"
  kubectl apply -f "${SCRIPT_DIR}/k8s/secrets.yaml"
  kubectl apply -f "${SCRIPT_DIR}/k8s/observability.yaml"

  # Replace image placeholders in manifests with actual registry
  info "Substituting image registry in manifests..."
  for f in k8s/exchange.yaml k8s/frontend.yaml k8s/alerts-agent.yaml \
           k8s/planner-agent.yaml k8s/stopfinder-agent.yaml k8s/registry.yaml; do
    sed "s|\${DOCKER_REGISTRY}|${DOCKER_REGISTRY}|g" "${SCRIPT_DIR}/${f}" | kubectl apply -f -
  done

  info "Waiting for agents to be ready..."
  kubectl -n mbta wait --for=condition=ready pod -l app=alerts-agent --timeout=120s || true
  kubectl -n mbta wait --for=condition=ready pod -l app=planner-agent --timeout=120s || true
  kubectl -n mbta wait --for=condition=ready pod -l app=stopfinder-agent --timeout=120s || true
  kubectl -n mbta wait --for=condition=ready pod -l app=registry --timeout=120s || true

  # Register agents
  info "Registering agents in NANDA registry..."
  sed "s|\${DOCKER_REGISTRY}|${DOCKER_REGISTRY}|g" "${SCRIPT_DIR}/k8s/register-agents-job.yaml" | kubectl apply -f -

  info "Deployment complete!"
  echo ""
  info "Check status:  kubectl -n mbta get pods"
  info "Frontend URL:  kubectl -n mbta get svc frontend -o jsonpath='{.status.loadBalancer.ingress[0].ip}'"
}

# ─────────────────────────────────────────────
# Teardown
# ─────────────────────────────────────────────
destroy() {
  warn "Deleting all MBTA resources from Kubernetes..."
  kubectl delete namespace mbta --ignore-not-found
  info "Namespace 'mbta' deleted."

  warn "Cleaning up local Docker images..."
  docker rmi "${DOCKER_REGISTRY}/mbta-exchange:${TAG}" >/dev/null 2>&1 || true
  docker rmi "${DOCKER_REGISTRY}/mbta-agent:${TAG}" >/dev/null 2>&1 || true
  docker rmi "${DOCKER_REGISTRY}/mbta-registry:${TAG}" >/dev/null 2>&1 || true

  if [[ "${USE_BUILDX}" == "true" ]]; then
    warn "Removing buildx builder (mbta-builder)..."
    docker buildx rm mbta-builder >/dev/null 2>&1 || true
  fi

  # Optional: remove remote images from Docker Hub
  if [[ "${DOCKER_REGISTRY}" == docker.io/* ]]; then
    if get_dockerhub_creds; then
      DOCKERHUB_NAMESPACE="${DOCKER_REGISTRY#docker.io/}"
      warn "Deleting remote images from Docker Hub (${DOCKERHUB_NAMESPACE})..."
      for repo in mbta-exchange mbta-agent mbta-registry; do
        curl -s -u "${DOCKERHUB_USERNAME}:${DOCKERHUB_TOKEN}" \
          -X DELETE "https://hub.docker.com/v2/repositories/${DOCKERHUB_NAMESPACE}/${repo}/tags/${TAG}/" \
          >/dev/null || true
      done
      info "Docker Hub cleanup attempted for tag ${TAG}."
    else
      warn "Docker Hub credentials not set. Skipping remote delete."
      warn "Set DOCKERHUB_USERNAME and DOCKERHUB_TOKEN or use docker login."
    fi
  else
    warn "Remote delete is only automated for Docker Hub. Skipping remote cleanup."
  fi
}

# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────
case "${1:-all}" in
  build)   build ;;
  push)    push ;;
  apply)   apply ;;
  destroy) destroy ;;
  all)
    build
    push
    apply
    ;;
  *)
    echo "Usage: $0 [build|push|apply|destroy|all]"
    exit 1
    ;;
esac
