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
  sim_run_id?: number | null;
  fingerprint: string;
  category: string;
  severity: Severity;
  title: string;
  message?: string | null;
  entity_type?: string | null;
  entity_id?: string | null;
  fuel_type?: Fuel | null;
  status: "OPEN" | "ACKNOWLEDGED" | "RESOLVED";
  opened_tick?: number | null;
  resolved_tick?: number | null;
  opened_at: string;
  resolved_at?: string | null;
  acknowledged_by?: string | null;
  data?: Record<string, unknown> | null;
  related_decision_id?: number | null;
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
  incidents?: {
    id: number;
    kind?: string;
    component?: string;
    status: string;
    started_at?: string;
    summary?: string;
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

export interface DecisionLine {
  id: number;
  decision_id: number;
  line_no: number;
  source_depot_id: string;
  destination_station_id: string;
  route_id: string;
  fuel_type: Fuel;
  quantity: number;
  idempotency_key: string;
  status: LineStatus;
  sim_allocation_id?: number | null;
  sim_error_code?: string | null;
  expected_arrival_tick?: number | null;
  actual_arrival_tick?: number | null;
  received_liters?: number | null;
  reason?: string | null;
  created_at?: string;
}

export interface DecisionOutcome {
  decision_id: number;
  evaluated_tick: number;
  predicted?: Record<string, unknown> | null;
  realised?: Record<string, unknown> | null;
  hit?: boolean | null;
  created_at?: string;
}

export interface AutopilotEligibility {
  eligible: boolean;
  blocked_reasons: string[];
  requires_four_eyes?: boolean;
}

export interface DecisionExplanation {
  autopilot_blocked_reason?: string;
  autopilot_eligibility?: AutopilotEligibility;
  [key: string]: any;
}

export interface Decision {
  id: number;
  sim_run_id?: number | null;
  tick: number;
  policy: string;
  policy_version?: string;
  trigger?: string;
  status: DecisionStatus;
  mode?: string;
  confidence?: number;
  degraded?: boolean;
  fallback_reason?: string | null;
  narrative?: string | null;
  approved_by?: string | null;
  approved_at?: string | null;
  rejected_reason?: string | null;
  expires_at_tick?: number | null;
  created_at?: string;
  explanation?: DecisionExplanation | Record<string, any> | null;
  impact?: Record<string, unknown> | null;
  objective?: Record<string, unknown> | null;
  lines: DecisionLine[];
  line_count?: number;
  active_line_count?: number;
  total_liters?: number;
  outcome?: DecisionOutcome | null;
}

export interface DecisionsResponse {
  items: Decision[];
  total: number;
}

export interface RecommendationsResponse {
  items: Decision[];
}

export interface AuditLogEntry {
  id: number;
  at: string;
  actor: string;
  action: string;
  entity_type?: string | null;
  entity_id?: string | null;
  payload?: Record<string, unknown> | null;
}

export interface AuditResponse {
  items: AuditLogEntry[];
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

export interface RiskRow {
  station_id: string;
  station_name?: string;
  region_id?: string;
  fuel_type: Fuel;
  risk_level: RiskLevel;
  hours_to_stockout: number;
  stockout_prob: number;
  inventory: number;
  drivers: string[];
}

export interface RiskResponse {
  tick: number;
  rows: RiskRow[];
}

export interface AlertsResponse {
  items: Alert[];
  total: number;
}

export interface CoverMetric {
  available: number;
  demand_96: number;
  cover_days: number;
}

export interface CoverResponse {
  network: Record<Fuel, CoverMetric>;
  regions: Record<string, Record<Fuel, CoverMetric>>;
  supply_ends_tick: number;
  scarcity: Record<Fuel, boolean>;
}

export interface ForecastPoint {
  tick: number;
  p10: number;
  p50: number;
  p90: number;
}

export interface ForecastHistoryPoint {
  tick: number;
  demand: number;
}

export interface ForecastSeries {
  station_id: string;
  fuel_type: Fuel;
  model: string;
  is_fallback: boolean;
  history: ForecastHistoryPoint[];
  points: ForecastPoint[];
  coverage_80: number;
}

export interface Incident {
  id: number;
  kind?: string | null;
  component?: string | null;
  status: string;
  started_at?: string | null;
  mitigated_at?: string | null;
  resolved_at?: string | null;
  summary?: string | null;
  timeline?: unknown[];
  created_at: string;
}

export interface IncidentsResponse {
  items: Incident[];
}

export interface ReconciliationLine {
  line_id: number;
  sent: number;
  received: number;
  loss: number;
  loss_reason: string;
  method: string;
}

export interface StationVariance {
  station_id: string;
  fuel_type: string;
  variance_cusum: number;
  alarm: boolean;
}

export interface ReconciliationReport {
  lines: ReconciliationLine[];
  totals: {
    sent: number;
    received: number;
    loss: number;
    loss_rate: number;
  };
  stations: StationVariance[];
}

export interface WhatIfScenarioStation {
  station_id: string;
  fuel_type: string;
  stockout_prob: number;
  first_stockout_tick?: number | null;
  inventory_p50: number[];
  initial_inv?: number;
  ending_inv?: number;
  delta_liters?: number;
  unmet_liters?: number;
  overflow_liters?: number;
}

export interface WhatIfScenario {
  name: string;
  unmet: number;
  overflow: number;
  min_station_sl: number;
  stations: WhatIfScenarioStation[];
}

export interface WhatIfResponse {
  tick: number;
  scenarios: WhatIfScenario[];
  disclaimer: string;
}

export interface WhyAtRiskItem {
  signal: string;
  value: string | number;
  detail: string;
}

export interface ExpectedImpact {
  stockout_prob_before: number;
  stockout_prob_after: number;
  unmet_before?: number;
  unmet_after?: number;
}

export interface AlternativeItem {
  name: string;
  stockout_prob_after?: number;
  why_not?: string;
}

export interface Explanation {
  headline: string;
  why_at_risk: WhyAtRiskItem[];
  constraints: string[];
  expected_impact: ExpectedImpact;
  confidence: number;
  confidence_factors: Record<string, number>;
  alternatives: AlternativeItem[];
  policy: string;
  forecast_model: string;
  degraded: boolean;
  sources?: Array<{ type: string; id: string }>;
}

export interface AssistantSource {
  type: string;
  id: string;
}

export interface AssistantQueryRequest {
  question: string;
}

export interface AssistantResponse {
  answer: string;
  engine: "llm" | "rules";
  sources: AssistantSource[];
  generated_at: string;
}

export interface SummaryStats {
  window_ticks: number;
  service_level: number;
  unmet_liters: number;
  total_dispatched_liters: number;
  decisions_count: number;
  autopilot_count: number;
  manual_count: number;
  alerts_opened: number;
  alerts_resolved: number;
  incidents_count: number;
  active_policy: string;
}

export interface SummaryResponse {
  text: string;
  engine: "llm" | "template";
  stats: SummaryStats;
}

export interface ControlPreset {
  title: string;
  description: string;
  events: Array<{
    type: string;
    duration_ticks: number;
    parameters: Record<string, unknown>;
  }>;
}

export interface ControlStatus {
  demo_controls_enabled: boolean;
  sim_status: "RUNNING" | "PAUSED" | "STOPPED" | "DISABLED" | string;
  tick: number;
  sim_time: string | null;
  scenario_id?: string;
  active_events_count: number;
  active_events: Array<{
    id: number;
    type: string;
    start_tick: number;
    end_tick: number;
    status: string;
    parameters: Record<string, unknown>;
  }>;
  active_faults?: Array<Record<string, unknown>>;
  presets: Record<string, ControlPreset>;
  faults: Record<string, string>;
}

export interface ControlLogEntry {
  id: number;
  at: string;
  actor: string;
  action: string;
  status: "SUCCESS" | "FAILED" | string;
  result: string;
  details?: Record<string, unknown>;
}


