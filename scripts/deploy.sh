#!/usr/bin/env bash
# ==============================================================================
# scripts/deploy.sh — FSIP Automated Production Deployment & Rollback (Phase 13)
# ==============================================================================
set -euo pipefail

TARGET_VERSION="${1:-}"
if [ -z "$TARGET_VERSION" ]; then
  echo "Usage: $0 <VERSION>"
  echo "Example: $0 v1.0.0"
  exit 1
fi

VERSIONS_FILE="deploy/versions.env"
if [ ! -f "$VERSIONS_FILE" ]; then
  echo "ERROR: Versions configuration file '$VERSIONS_FILE' not found."
  exit 1
fi

PREV_VERSION=$(grep '^APP_VERSION=' "$VERSIONS_FILE" | head -n1 | cut -d= -f2)
echo "=== FSIP Safe Deployment Initiated ==="
echo "Current Version: $PREV_VERSION"
echo "Target Version:  $TARGET_VERSION"

# Concurrency lock
LOCK_FILE="/tmp/fsip_deploy.lock"
if [ -e "$LOCK_FILE" ]; then
  echo "ERROR: Another deployment is currently in progress ($LOCK_FILE exists)."
  exit 1
fi
trap 'rm -f "$LOCK_FILE"' EXIT
touch "$LOCK_FILE"

rollback() {
  echo ""
  echo "⚠️  DEPLOYMENT FAILED: Rolling back to previous version '$PREV_VERSION'..."
  sed -i.bak "s/^APP_VERSION=.*/APP_VERSION=${PREV_VERSION}/" "$VERSIONS_FILE" || sed -i "" "s/^APP_VERSION=.*/APP_VERSION=${PREV_VERSION}/" "$VERSIONS_FILE"
  rm -f "${VERSIONS_FILE}.bak"

  docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file "$VERSIONS_FILE" up -d
  echo "Waiting for services to recover after rollback..."
  sleep 5
  bash scripts/smoke_test.sh
  echo "✓ Rollback to '$PREV_VERSION' completed successfully. System is healthy."
  exit 1
}

# 1. Update version file with target version
sed -i.bak "s/^APP_VERSION=.*/APP_VERSION=${TARGET_VERSION}/" "$VERSIONS_FILE" || sed -i "" "s/^APP_VERSION=.*/APP_VERSION=${TARGET_VERSION}/" "$VERSIONS_FILE"
rm -f "${VERSIONS_FILE}.bak"

echo "Applying production compose configuration..."
if ! docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file "$VERSIONS_FILE" up -d; then
  rollback
fi

echo "Waiting for services to become healthy..."
sleep 5

# 2. Run smoke check
echo "Executing smoke test verification..."
if ! bash scripts/smoke_test.sh; then
  rollback
fi

echo "=========================================================="
echo "✓ Deployment of FSIP '$TARGET_VERSION' succeeded and verified!"
echo "=========================================================="
exit 0
