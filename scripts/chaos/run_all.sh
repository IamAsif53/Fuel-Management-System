#!/usr/bin/env bash
set -euo pipefail

echo "=========================================================="
echo "      FSIP Automated Chaos Drill Suite (Phase 10)         "
echo "=========================================================="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

export PYTHONPATH="${REPO_ROOT}/services/api:${REPO_ROOT}/libs/fsip_common:${REPO_ROOT}/services/engine:${REPO_ROOT}/services/intelligence"

if [ -f "${REPO_ROOT}/.venv/Scripts/python.exe" ]; then
    PYTHON_BIN="${REPO_ROOT}/.venv/Scripts/python.exe"
elif [ -f "${REPO_ROOT}/.venv/bin/python" ]; then
    PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
else
    PYTHON_BIN="python3"
fi

"${PYTHON_BIN}" "${SCRIPT_DIR}/chaos_runner.py"

echo "=========================================================="
echo "Drill suite completed successfully."
echo "Evidence recorded at: docs/evidence/resilience.md"
echo "=========================================================="
