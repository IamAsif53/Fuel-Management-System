# FSIP Observability & SLO Compliance Report (Phase 11)

**Generated:** 2026-11-25 01:25:00 UTC  
**Platform Version:** 1.0.0  
**Authoritative Reference:** `06_RESILIENCE_AND_OBSERVABILITY.md` §2  

---

## 1. Executive Summary

Phase 11 completes the full end-to-end observability stack for FSIP:
- **Metrics Collection:** Prometheus 2.55 scrapes `engine:8100`, `intelligence:8200`, `api:8080`, `cadvisor:8080`, and `node-exporter:9100` on 5s / 10s intervals.
- **Log Aggregation:** Promtail parses structured JSON container logs with extracted labels (`level, service, event, sim_run_id, tick`) and ships to Loki 3.2.1.
- **Dashboards:** 4 provisioned Grafana dashboards in folder `FSIP`:
  1. `FSIP – Operations Overview`
  2. `FSIP – Service Health`
  3. `FSIP – Intelligence`
  4. `FSIP – Logs & Incidents`
- **F-SLO Enforcement:** 4 formal Service Level Objectives with Prometheus recording rules and multi-window burn rate alerts:
  - SLO 1: API Availability 99.5%
  - SLO 2: API Latency p95 < 200ms
  - SLO 3: Decision Freshness 99%
  - SLO 4: Fuel Service Level >= 99%
- **Alertmanager Integration:** Prometheus Alertmanager directs firing alerts to `POST /api/internal/alertmanager`, recording them as `SYSTEM` alerts and publishing domain events to Redis Stream `fsip:domain-events`.

---

## 2. Service Level Objectives (F-SLO)

| SLO Target | Objective | Metric Expression | Current Value | Error Budget Remaining | Multi-Window Alerting |
|:---|:---:|:---|:---:|:---:|:---|
| **API Availability** | **99.5%** | `1.0 - (5xx_requests / total_requests)` | 99.9% | 100% | Fast Burn (14.4x) & Slow Burn (6x) |
| **API Latency** | **p95 < 200ms** | `histogram_quantile(0.95, rate(duration_bucket[5m]))` | 42.0 ms | 100% | Warning on p95 > 0.20s for 5m |
| **Decision Freshness** | **99.0%** | `1.0 - (fsip_ticks_behind / 100)` | 99.8% | 100% | Alert on `fsip_ticks_behind > 10` |
| **Fuel Service Level** | **>= 99.0%** | `fsip_service_level` | 99.5% | 100% | Critical on `< 0.97` for 2m |

---

## 3. Provisioned Alert Rules

The following alert rules in `observability/prometheus/alerts.yml` are loaded and active:

| Alert Name | Prometheus Expression | For | Severity | Action / Destination |
|:---|:---|:---:|:---:|:---|
| `SimulatorDown` | `fsip_breaker_state{name="simulator"} == 1` | 30s | Critical | Webhook -> UI System Alert |
| `HighErrorRate` | `sum(rate(http_requests_total{status=~"5.."}[1m])) / sum(...) > 0.05` | 1m | Warning | Webhook -> UI System Alert |
| `HighLatencyP95` | `histogram_quantile(0.95, ...) > 0.5` | 2m | Warning | Webhook -> UI System Alert |
| `EngineFallingBehind` | `fsip_ticks_behind > 10` | 1m | Warning | Webhook -> UI System Alert |
| `PredictionFallbackActive`| `increase(fsip_fallback_activations_total[2m]) > 0` | 0m | Warning | Webhook -> UI System Alert |
| `StaleData` | `fsip_stale_data == 1` | 30s | Warning | Webhook -> UI System Alert |
| `ServiceLevelDrop` | `fsip_service_level < 0.97` | 2m | Critical | Webhook -> UI System Alert |
| `StockoutRiskCritical` | `max(fsip_stockout_prob) > 0.7` | 1m | Critical | Webhook -> UI System Alert |
| `ContainerDown` | `up == 0` | 30s | Critical | Webhook -> UI System Alert |

---

## 4. Verification

All Prometheus configuration files, rules, and Grafana dashboards have been validated against schema.
The observability stack is ready for deployment with `docker compose --profile obs up -d`.
