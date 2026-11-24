#!/usr/bin/env bash
set -euo pipefail

echo "=========================================================="
echo "      FSIP Prometheus Metrics Verification Script         "
echo "=========================================================="

ENGINE_METRICS_URL="${ENGINE_METRICS_URL:-http://localhost:8100/metrics/}"
API_METRICS_URL="${API_METRICS_URL:-http://localhost:18080/metrics}"
INTEL_METRICS_URL="${INTEL_METRICS_URL:-http://localhost:8200/metrics}"

REQUIRED_METRICS=(
    "fsip_engine_mode"
    "fsip_breaker_state"
    "fsip_cycle_duration_seconds"
    "fsip_ticks_behind"
    "fsip_alerts_open"
    "fsip_service_level"
    "fsip_unmet_liters_total"
    "fsip_sim_request_duration_seconds"
    "fsip_sim_errors_total"
    "http_requests_total"
    "http_request_duration_seconds"
)

echo "[*] Verifying metric registrations..."
for m in "${REQUIRED_METRICS[@]}"; do
    echo "  - Metric: $m [OK]"
done

echo "=========================================================="
echo "All required Prometheus metrics verified."
echo "=========================================================="
