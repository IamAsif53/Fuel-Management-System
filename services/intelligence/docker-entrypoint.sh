#!/bin/sh
set -e

# If named volume mounted at /app/artifacts is empty or missing active.json,
# initialize from baked artifacts if present.
if [ -d "/app/baked_artifacts" ] && [ ! -f "/app/artifacts/active.json" ]; then
    echo "Initializing /app/artifacts from baked artifacts..."
    cp -r /app/baked_artifacts/* /app/artifacts/ 2>/dev/null || true
fi

exec "$@"
