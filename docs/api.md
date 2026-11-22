# Fuel Supply Intelligence & Resilience Platform — API Documentation

**Service:** `services/api` (`fsip-api`)  
**Base URL:** `http://localhost:8080` (or proxy via Frontend at `http://localhost:3000/api`)  
**Interactive Docs:** [`/api/docs`](http://localhost:8080/api/docs) (Swagger UI)  
**OpenAPI Specification:** [`/api/openapi.json`](http://localhost:8080/api/openapi.json)

---

## 1. Overview & Architecture

The API service provides public and operator interfaces for the Fuel Supply Intelligence & Resilience Platform. It is built using FastAPI and communicates with:
- **Redis (`fsip-redis:6379`)**: Real-time snapshot cache (`fsip:state`), tick counter (`fsip:state:tick`), health status hash (`fsip:health`), domain event stream (`fsip:domain-events`), and rate limit counters (`fsip:ratelimit:{ip}`).
- **TimescaleDB (`fsip-timescaledb:5432`)**: Read-only persistence fallback for historical queries and world snapshots (`world_snapshots`, `demand_observations`).
- **Internal Services**: Probes `decision_engine` (port 8100), `prediction_service` (port 8200), and `fuel_simulator` (port 8000).

---

## 2. Common Headers & Protocol Conventions

All state and entity responses provide the following custom HTTP response headers:

| Header | Description | Example |
|---|---|---|
| `X-Data-Tick` | Simulation tick of the data served | `140` |
| `X-Degraded` | Boolean indicating whether data was served from a degraded state (e.g. Redis outage fallback to database) | `false` or `true` |
| `ETag` | Entity tag representing the simulation run and tick | `"3-140"` |

### HTTP 304 Not Modified
Endpoints returning state support the `If-None-Match` request header. If the client sends an ETag matching the current `"{sim_run_id}-{tick}"`, the server immediately responds with `304 Not Modified` and an empty response body.

---

## 3. Rate Limiting & Protection

All requests (except health probes, metrics, and docs) are subject to rate limiting:
- **Quota:** Configurable via `API_RATE_LIMIT_PER_MIN` (default: 120 requests/minute per client IP).
- **Backend:** Redis counter with sliding 60-second TTL (`fsip:ratelimit:{ip}`).
- **Fallback:** In-memory sliding counter if Redis is unavailable.
- **Rate Limit Response:**
  - Status Code: `429 Too Many Requests`
  - Header: `Retry-After: 60`
  - Body:
    ```json
    {
      "detail": {
        "code": "RATE_LIMITED",
        "message": "Rate limit exceeded"
      }
    }
    ```

---

## 4. Authentication & Authorization

| Role | Access Level | Description |
|---|---|---|
| **Viewer** | Open (No Token) | All read endpoints (`/api/state`, `/api/stream`, `/api/stations/{id}`, `/api/depots/{id}`, `/api/system/status`). |
| **Operator** | `Authorization: Bearer <token>` | Operator write and control endpoints. Accepts static token matching `API_OPERATOR_TOKEN` or a valid HS256 JWT signed with `API_JWT_SECRET` containing role `operator` or `supervisor`. |

---

## 5. Endpoints Reference

### 5.1 Health & Operational Probes

#### `GET /health/live`
Process liveness probe.
- **Status Code:** `200 OK`
- **Response:**
  ```json
  {
    "status": "ok",
    "service": "api"
  }
  ```

#### `GET /health/ready`
Readiness probe verifying Redis and Database connectivity.
- **Status Code:** `200 OK` (when Redis or DB is available) or `503 Service Unavailable` (when both are down)
- **Response:**
  ```json
  {
    "status": "ready",
    "redis": "UP",
    "database": "UP"
  }
  ```
  *(If Redis is down but DB is up, returns `status: "degraded"` with 200 OK)*.

#### `GET /metrics`
Prometheus metrics exposition endpoint (standard text format) containing:
- `http_requests_total{service="api", route="/api/...", method="GET", status="200"}`
- `http_request_duration_seconds{service="api", route="/api/..."}`

#### `GET /api/ping`
Basic ping check.
- **Response:**
  ```json
  {
    "pong": true,
    "version": "dev"
  }
  ```

---

### 5.2 State & Streaming Endpoints

#### `GET /api/state`
Returns the complete public `WorldState` snapshot.
- **Headers Sent:** `X-Data-Tick`, `X-Degraded`, `ETag`
- **Headers Accepted:** `If-None-Match`
- **Primary Source:** Redis key `fsip:state`
- **Fallback Source:** TimescaleDB table `world_snapshots` (sets `X-Degraded: true` and `degraded_reasons: ["redis_down"]`)
- **Response (200 OK):**
  ```json
  {
    "sim_run_id": 3,
    "tick": 140,
    "sim_time": "2026-01-02T11:00:00Z",
    "tick_minutes": 15,
    "sim_status": "RUNNING",
    "clock_label": "Day 2 11:00 · t=140",
    "generated_at": "2026-11-20T10:16:03Z",
    "age_seconds": 0.4,
    "stale": false,
    "degraded": false,
    "degraded_reasons": [],
    "engine_mode": "NORMAL",
    "operating_mode": "advisory",
    "metrics": {
      "service_level": 0.998,
      "served": 1,
      "unmet": 0,
      "allocation_liters": 0,
      "allocation_failures": 0,
      "overflow_liters_est": 0
    },
    "depots": [...],
    "stations": [...],
    "routes": [...],
    "events": [...],
    "supply_arrivals_upcoming": [...],
    "allocations_active": [...],
    "alerts_open": [...],
    "pending_recommendations": [...],
    "system": {
      "simulator": "UP",
      "sse": "CONNECTED",
      "intelligence": "UP",
      "db": "UP",
      "redis": "UP"
    }
  }
  ```

#### `GET /api/stream`
Server-Sent Events (SSE) real-time domain event stream.
- **Content-Type:** `text/event-stream`
- **Headers Accepted:** `Last-Event-ID` (for missed event replay)
- **Primary Source:** Redis Stream `fsip:domain-events`
- **Fallback Source:** TimescaleDB polling every 2 seconds when Redis is unavailable.
- **Throttling:** Max 2 events/second per client.
- **Heartbeat:** Sends `: keepalive\n\n` comment every 15 seconds of inactivity.
- **Event Format:**
  ```text
  id: 1732332800000-0
  event: state.updated
  data: {"tick": 141, "etag": "3-141"}

  : keepalive
  ```

---

### 5.3 Entity Detail Endpoints

#### `GET /api/stations/{station_id}`
Returns station detail, inventory history, and recent demand observations.
- **Query Parameters:**
  - `history_ticks` (integer, default: 96, min: 1, max: 500)
- **Response (200 OK):**
  ```json
  {
    "station": {
      "id": "station-mirpur",
      "name": "Mirpur Fuel Station",
      "region_id": "dhaka_north",
      "status": "OPEN",
      "inventory": {"DIESEL": 12000.0, "OCTANE": 8000.0},
      "capacity": {"DIESEL": 20000.0, "OCTANE": 10000.0},
      "fill_pct": {"DIESEL": 0.60, "OCTANE": 0.80},
      "risk": {
        "DIESEL": {"level": "LOW", "hours_to_stockout": 24.0, "stockout_prob": 0.05}
      }
    },
    "history": [
      {"tick": 139, "inventory": {"DIESEL": 12200.0, "OCTANE": 8100.0}},
      {"tick": 140, "inventory": {"DIESEL": 12000.0, "OCTANE": 8000.0}}
    ],
    "demand": [
      {
        "tick": 140,
        "sim_time": "2026-01-02T11:00:00Z",
        "station_id": "station-mirpur",
        "fuel_type": "DIESEL",
        "demand": 200.0,
        "served": 200.0,
        "unmet": 0.0
      }
    ],
    "forecast": []
  }
  ```
- **Error Response (404 Not Found):**
  ```json
  {
    "detail": {
      "code": "STATION_NOT_FOUND",
      "message": "Station 'station-unknown' not found"
    }
  }
  ```

#### `GET /api/depots/{depot_id}`
Returns depot detail and historical inventory levels.
- **Query Parameters:**
  - `history_ticks` (integer, default: 96, min: 1, max: 500)
- **Response (200 OK):**
  ```json
  {
    "depot": {
      "id": "depot-gazipur",
      "name": "Gazipur Depot",
      "region_id": "dhaka_north",
      "status": "OPEN",
      "capacity": {"DIESEL": 90000.0, "OCTANE": 45000.0},
      "inventory": {"DIESEL": 75000.0, "OCTANE": 38000.0},
      "dispatch_capacity_per_tick": 20000.0
    },
    "history": [
      {"tick": 139, "inventory": {"DIESEL": 76000.0, "OCTANE": 38500.0}},
      {"tick": 140, "inventory": {"DIESEL": 75000.0, "OCTANE": 38000.0}}
    ],
    "projection": []
  }
  ```
- **Error Response (404 Not Found):**
  ```json
  {
    "detail": {
      "code": "DEPOT_NOT_FOUND",
      "message": "Depot 'depot-unknown' not found"
    }
  }
  ```

---

### 5.4 System Status & Observability

#### `GET /api/system/status`
Aggregates health across all platform components, Redis hash metadata (`fsip:health`), and in-process rolling window SLO metrics.
- **Response (200 OK):**
  ```json
  {
    "overall": "Healthy",
    "engine_mode": "advisory",
    "components": {
      "backend_api": {
        "status": "Healthy",
        "p95_ms": 12.4,
        "error_rate": 0.0
      },
      "database": {
        "status": "Healthy"
      },
      "redis": {
        "status": "Healthy"
      },
      "fuel_simulator": {
        "status": "Healthy",
        "latency_ms": 4.2
      },
      "prediction_service": {
        "status": "Healthy",
        "latency_ms": 6.8
      },
      "decision_engine": {
        "status": "Healthy",
        "latency_ms": 5.1,
        "engine_mode": "advisory",
        "tick": 140,
        "last_sync_age_s": 0.5
      },
      "notifier": {
        "status": "Healthy"
      }
    },
    "versions": {
      "api": "dev",
      "engine": "dev",
      "intelligence": "dev",
      "frontend": "dev",
      "git_sha": "unknown"
    },
    "p95_latency_ms": 12.4,
    "error_rate": 0.0,
    "slo": [
      {
        "name": "API Availability",
        "target": 0.999,
        "current": 1.0,
        "budget_remaining": 1.0
      },
      {
        "name": "p95 Latency",
        "target": 500.0,
        "current": 12.4,
        "budget_remaining": 1.0
      }
    ]
  }
  ```

---

## 6. Domain Events Specification

Events published to Redis Stream `fsip:domain-events` and streamed via `/api/stream`:

| Event Type | Payload Schema | Description |
|---|---|---|
| `state.updated` | `{"tick": N, "etag": "<run>-<tick>"}` | Signals a new simulation tick. Triggers UI to re-fetch `/api/state` using `If-None-Match`. |
| `alert.opened` / `.updated` / `.resolved` | `Alert` object | Stockout, buffer breach, or disruption alerts. |
| `decision.created` / `.approved` / `.rejected` / `.executed` / `.outcome` | `Decision` summary object | Allocation decision lifecycle. |
| `incident.opened` / `.updated` / `.resolved` | `Incident` object | High-level incident coordination. |
| `mode.changed` | `{"engine_mode": "...", "operating_mode": "..."}` | Normal vs Degraded state machine transition. |
| `scarcity.entered` / `.exited` | `{"fuel_type": "...", "cover_ratio": ...}` | Scarcity policy activation. |
| `reset.detected` | `{"old_run_id": N, "new_run_id": M}` | Simulation reset notification. |

---

## 7. Error Format Standard

All errors adhere to the locked error format:

```json
{
  "detail": {
    "code": "STRING_ERROR_CODE",
    "message": "Human-readable description of the error",
    "errors": []
  }
}
```
Common error codes:
- `UNAUTHORIZED`: Missing or invalid Bearer token.
- `FORBIDDEN`: Token lacks required role permissions.
- `RATE_LIMITED`: Request rate quota exceeded.
- `STATION_NOT_FOUND`: Specified station ID does not exist.
- `DEPOT_NOT_FOUND`: Specified depot ID does not exist.
- `VALIDATION_ERROR`: Invalid query or path parameters.
