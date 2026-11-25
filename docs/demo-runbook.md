# FSIP — Live Demonstration Runbook
## Step-by-Step Operator & Jury Presentation Guide

**Target System:** Fuel Supply Intelligence & Resilience Platform (FSIP)  
**Version:** 1.0.0  
**Demonstration Length:** 10–15 minutes  

---

## 1. Objectives of this Demonstration

1. Show continuous, autonomous operational sensing and reconciliation across distributed fuel supply chains.
2. Demonstrate real-time risk quantification, demand forecasting, and conformal uncertainty bands.
3. Validate deterministic Mixed-Integer Linear Programming (MILP) dispatch generation under fleet and route constraints.
4. Showcase cognitive forcing safeguards for human-in-the-loop decision approvals.
5. Prove rapid fault tolerance, automatic state degradation, and recovery through chaos injection.
6. Verify emergency access via zero-dependency Standalone Break-Glass tooling.

---

## 2. Pre-Flight Verification & Health Check

### 2.1. Verify Environment & Docker Stack
Open a terminal in the repository root (`f:\NewTask\fuel-platform`):

```bash
# Check running containers
docker compose ps
```
*Expected Output:* All core containers (`simulator`, `timescaledb`, `redis`, `engine`, `intelligence`, `api`, `frontend`, `prometheus`, `grafana`, `alertmanager`, `loki`, `cadvisor`) report status `Up (healthy)`.

### 2.2. Verify API Readiness
```bash
curl -s http://localhost:18080/health/ready
```
*Expected Output:*
```json
{"status":"ready","redis":"UP","database":"UP"}
```

### 2.3. Open Interfaces
Open the following browser tabs:
- **Operations Console (UI):** `http://localhost:3000`
- **Grafana Dashboards:** `http://localhost:13001` (login: `admin` / `admin`)
- **API Swagger Documentation:** `http://localhost:18080/docs`
- **Break-Glass Console:** `http://localhost:3000/break-glass.html` (or `frontend/public/break-glass.html`)

---

## 3. Demonstration Script (Step-by-Step)

### Step 1: Real-Time Network Topology & Telemetry (2 minutes)
1. **Navigate to:** `http://localhost:3000/` (Network View).
2. **Observe:**
   - 10 retail stations across northern, central, and southern corridors.
   - 2 regional bulk depots (`depot-1` with 80,000L capacity, `depot-2` with 60,000L capacity).
   - Live tick counter advancing every 10 seconds ($t = 1, 2, \dots, 288$).
   - Active tanker icons traversing routes with dynamic progress bars.
3. **Key Talking Point:**
   *"FSIP does not just poll state; every 10 seconds, the engine executes F-RECON to reconcile physical simulator telemetry against internal dispatch ledgers, detecting lost volume or delayed shipments instantly."*

### Step 2: Demand Forecasting & Conformal Bands (2 minutes)
1. **Navigate to:** `http://localhost:3000/` -> Select station `station-01` -> View Forecast Panel.
2. **Observe:**
   - Historical consumption series plotted alongside predicted demand.
   - P10, P50, and P90 quantile curves.
   - Conformal prediction bounds providing guaranteed 90% coverage for safety stock sizing.
3. **Key Talking Point:**
   *"Our ML engine runs multi-horizon LightGBM and ARIMA models. If extreme volatility or sensor drift is detected via Page-Hinkley, the system automatically falls back to seasonal medians without operator intervention."*

### Step 3: Risk Alerts & Cognitive Forcing Decision Approval (3 minutes)
1. **Navigate to:** `http://localhost:3000/decisions` (Decision Ledger).
2. **Review Pending Recommendations:**
   - Locate an algorithmic recommendation with high quantity ($>5000\text{L}$) or flagged with confidence $<0.7$.
3. **Demonstrate Cognitive Guardrail:**
   - Attempt to click **Approve** without checking the driver review confirmation checkbox.
   - *Observe:* The system blocks submission and highlights: *"Cognitive Forcing Required: High-impact dispatches require explicit verification of driver and tanker safety."*
   - Check the verification box and click **Approve**.
   - *Observe:* Status immediately flips to `APPROVED`, allocation is dispatched to the simulator via `/v1/allocations`, and an audit event is published.
4. **Key Talking Point:**
   *"FSIP prevents 'rubber-stamping' automation bias. Operators are forced to actively verify boundary conditions when critical thresholds are crossed."*

### Step 4: What-If Sandbox & Scarcity Planning (3 minutes)
1. **Navigate to:** `http://localhost:3000/what-if` (Scenario Sandbox).
2. **Adjust Parameters:**
   - Simulate a 50% demand surge across urban stations or simulate a complete closure of Route `route-1-3`.
3. **Click "Simulate Scenario":**
   - The engine runs shadow MILP optimization in $<500\text{ms}$.
   - View comparison table: Baseline stockouts vs. Scenario projected stockouts.
4. **Key Talking Point:**
   *"Operators can stress-test supply contingencies interactively before implementing rationing or route re-allocations."*

### Step 5: Resilience Drill & Break-Glass Recovery (3 minutes)
1. **Simulate Primary API / Engine Failure:**
   ```bash
   docker stop fsip-api-1 fsip-engine
   ```
2. **Observe UI State:**
   - Frontend displays connection warning banner indicating backend services unreachable.
3. **Open Break-Glass Emergency Console:**
   - Open `frontend/public/break-glass.html` directly in the browser.
   - Note that it loads instantly with zero server dependencies.
   - Enter Simulator URL (`http://localhost:8000`), source depot, destination station, and 2000L.
   - Click **Emergency Dispatch**.
   - *Observe:* Direct HTTP 201 dispatch to the simulator with emergency idempotency tag.
4. **Restore Services:**
   ```bash
   docker start fsip-timescaledb fsip-redis fsip-engine fsip-api-1
   ```
   - Watch engine seamlessly reconcile the manual break-glass dispatch upon reboot without duplicate allocations.

---

## 4. Teardown & Clean State Reset

```bash
# Verify all services returned to healthy state
docker compose ps

# If resetting simulation run for another rehearsal:
curl -X POST http://localhost:8000/v1/sim/reset
```
