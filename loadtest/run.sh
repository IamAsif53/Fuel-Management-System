#!/usr/bin/env bash
# Shell script to execute FSIP Phase 14 load tests using grafana/k6 Docker container
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="${SCRIPT_DIR}/out"
mkdir -p "${OUT_DIR}"

BASE_URL="${API_BASE_URL:-http://host.docker.internal:18080}"
TOKEN="${OPERATOR_TOKEN:-change-me-operator}"

echo "=========================================================="
echo "FSIP Phase 14: Load Testing & Performance Benchmarking"
echo "Target API: ${BASE_URL}"
echo "Output Directory: ${OUT_DIR}"
echo "=========================================================="

TESTS=(
    "dashboard_read.js:dashboard_read:200 VU Dashboard Polling (State, Risk, Decisions)"
    "decision_flow.js:decision_flow:50 VU Concurrent Operator Decision & Approval"
    "spike.js:spike:200 VU Sudden Traffic Spike & Recovery"
)

for entry in "${TESTS[@]}"; do
    IFS=":" read -r file name desc <<< "${entry}"
    echo ""
    echo ">>> Running: ${desc} (${file})..."
    docker run --rm \
        -v "${SCRIPT_DIR}:/loadtest" \
        -e "API_BASE_URL=${BASE_URL}" \
        -e "OPERATOR_TOKEN=${TOKEN}" \
        grafana/k6:latest run \
        --summary-export="/loadtest/out/${name}.json" \
        "/loadtest/k6/${file}"
    echo ">>> Test ${name} PASSED thresholds!"
done

echo ""
echo "=========================================================="
echo "All Phase 14 Load Test Benchmarks Completed Successfully!"
echo "=========================================================="
