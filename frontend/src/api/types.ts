/**
 * TypeScript mirror of backend API contracts (21_API_CONTRACTS.md §5).
 * Synchronized with Phase 02 backend implementation.
 */

export type Fuel = "DIESEL" | "PETROL" | "OCTANE";
export const FUELS: Fuel[] = ["DIESEL", "PETROL", "OCTANE"];
export type FuelMap = Record<Fuel, number>;

export type RiskLevel = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
export type Severity = "INFO" | "WARNING" | "CRITICAL";
export type HealthStatus = "Healthy" | "Degraded" | "Down";
export type EngineMode = "NORMAL" | "DEGRADED" | "SAFE_HOLD" | "NO_FEASIBLE_ROUTES";

export type DecisionStatus =
  | "PROPOSED"
  | "PENDING_APPROVAL"
  | "AWAITING_SECOND_APPROVAL"
  | "APPROVED"
  | "REJECTED"
  | "EXECUTED"
  | "PARTIAL"
  | "EXPIRED"
  | "FAILED";

export type LineStatus =
  | "PLANNED"
  | "SUBMITTED"
  | "DEFERRED"
  | "PENDING"
  | "IN_TRANSIT"
  | "ARRIVED"
  | "FAILED"
  | "CANCELLED"
  | "REJECTED_BY_SIM";

export interface Driver {
  signal: string;
  value: string | number;
  detail: string;
}

export interface RiskCell {
  level: RiskLevel;
  hours_to_stockout: number | null;
  stockout_prob_lead: number;
  stockout_prob_horizon: number;
  inventory: number;
  inflight: number;
  fill_pct: number;
  cover_hours: number | null;
  drivers: Driver[];
  forecast_model: string;
}

export interface StationView {
  id: string;
  name: string;
  region_id: string;
  status: "OPEN" | "OUTAGE" | string;
  demand_profile: string;
  demand_multiplier: number;
  capacity: FuelMap;
  inventory: FuelMap;
  inflight: FuelMap;
  fill_pct: FuelMap;
  risk: Record<Fuel, RiskCell>;
  routes_available: number;
  single_route: boolean;
}

export interface DepotView {
  id: string;
  name: string;
  region_id: string;
  status: "OPEN" | "CONSTRAINED" | string;
  dispatch_capacity_per_tick: number;
  dispatch_used_this_tick: number;
  capacity: FuelMap;
  inventory: FuelMap;
  next_supply: {
    id: string;
    fuel_type: Fuel;
    quantity: number;
    planned_tick: number;
    status: string;
  }[];
  cover_hours: Record<Fuel, number | null>;
  headroom_before_next_supply: FuelMap;
  constrained_self_cap: boolean;
}

export interface RouteView {
  id: string;
  source_depot_id: string;
  destination_station_id: string;
  transit_ticks: number;
  max_shipment: number;
  status: "AVAILABLE" | "DISRUPTED" | "SCHEDULED_DISRUPTION" | string;
  scheduled_disruption: {
    event_id: number;
    start_tick: number;
    end_tick: number;
  } | null;
}

export interface AllocationView {
  sim_allocation_id: number;
  decision_id: number | null;
  line_id: number | null;
  idempotency_key: string;
  route_id: string;
  source_depot_id: string;
  destination_station_id: string;
  fuel_type: Fuel;
  quantity: number;
  status: LineStatus;
  created_tick: number;
  departure_tick: number | null;
  expected_arrival_tick: number | null;
  progress: number;
}

export interface Alert {
  id: number;
  fingerprint: string;
  category: string;
  severity: Severity;
  status: "OPEN" | "ACKNOWLEDGED" | "RESOLVED";
  title: string;
  message: string;
  entity_type: string | null;
  entity_id: string | null;
  fuel_type: Fuel | null;
  opened_tick: number;
  resolved_tick: number | null;
  opened_at: string;
  acknowledged_by: string | null;
  related_decision_id: number | null;
  data: Record<string, unknown>;
}

export interface ComponentHealth {
  status: HealthStatus;
  latency_ms?: number;
  error?: string;
  breaker?: "CLOSED" | "OPEN" | "HALF_OPEN" | string;
  p95_ms?: number;
  error_rate?: number;
  last_sync_age_s?: number | null;
  engine_mode?: string;
  tick?: number;
  [k: string]: unknown;
}

export interface SystemStatus {
  overall: HealthStatus;
  engine_mode?: EngineMode | string;
  components: Record<string, ComponentHealth>;
  versions: Record<string, string>;
  p95_latency_ms?: number;
  error_rate?: number;
  slo?: {
    name: string;
    target: number;
    current: number;
    budget_remaining: number;
  }[];
}

export interface WorldStateMetrics {
  service_level: number;
  served_liters: number;
  unmet_liters: number;
  allocation_liters: number;
  allocation_failures: number;
  overflow_liters_est: number;
  depot_clipped_liters_est: number;
}

export interface WorldState {
  sim_run_id: number;
  tick: number;
  sim_time: string | null;
  tick_minutes: number;
  sim_status: "PAUSED" | "RUNNING" | "UNKNOWN" | string;
  clock_label: string;
  generated_at: string;
  age_seconds: number;
  stale: boolean;
  degraded: boolean;
  degraded_reasons: string[];
  engine_mode: EngineMode;
  operating_mode: "advisory" | "autopilot" | string;
  policy: string;
  scarcity: Record<Fuel, boolean>;
  metrics: WorldStateMetrics;
  depots: DepotView[];
  stations: StationView[];
  routes: RouteView[];
  events: {
    id: number;
    type: string;
    start_tick: number;
    end_tick: number;
    status: string;
    parameters: Record<string, unknown>;
  }[];
  supply_upcoming: {
    id: string;
    depot_id: string;
    fuel_type: Fuel;
    quantity: number;
    planned_tick: number;
    status: string;
  }[];
  allocations_active: AllocationView[];
  alerts_open_count: Record<Severity, number>;
  pending_recommendations: {
    decision_id: number;
    summary: string;
    confidence: number;
    total_liters: number;
    worst_level: RiskLevel;
    expires_at_tick: number;
  }[];
  bottleneck: { type: string; label: string } | null;
  system: Record<string, unknown>;
}

export interface StationHistoryEntry {
  tick: number;
  inventory: FuelMap;
}

export interface StationDemandEntry {
  id: number;
  sim_row_id: number;
  station_id: string;
  fuel_type: Fuel;
  tick: number;
  sim_time: string;
  demand_liters: number;
  served_liters: number;
  unmet_liters: number;
}

export interface StationDetailResponse {
  station: StationView;
  history: StationHistoryEntry[];
  demand: StationDemandEntry[];
  forecast: unknown[];
}

export interface DepotDetailResponse {
  depot: DepotView;
  history: StationHistoryEntry[];
  projection: unknown[];
}
