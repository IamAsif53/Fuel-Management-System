import type {
  WorldState,
  DepotView,
  StationView,
  RouteView,
  Fuel,
  RiskLevel,
} from "@/api/types";
import { liters } from "@/lib/format";

interface NetworkGraphProps {
  state: WorldState | null;
  onSelectEntity?: (entity: DepotView | StationView) => void;
}

interface Point {
  x: number;
  y: number;
}

const DEFAULT_POSITIONS: Record<string, Point> = {
  // Depots
  "depot-gazipur": { x: 140, y: 150 },
  gazipur: { x: 140, y: 150 },
  "depot-patiya": { x: 140, y: 380 },
  patiya: { x: 140, y: 380 },
  // Stations
  "station-mirpur": { x: 820, y: 70 },
  mirpur: { x: 820, y: 70 },
  "station-tongi": { x: 820, y: 210 },
  tongi: { x: 820, y: 210 },
  "station-karnaphuli": { x: 820, y: 330 },
  karnaphuli: { x: 820, y: 330 },
  "station-coxsbazar": { x: 820, y: 450 },
  coxsbazar: { x: 820, y: 450 },
};

const FUEL_COLORS: Record<Fuel, string> = {
  DIESEL: "var(--color-fuel-diesel)",
  PETROL: "var(--color-fuel-petrol)",
  OCTANE: "var(--color-fuel-octane)",
};

const RISK_BORDER_COLORS: Record<RiskLevel, string> = {
  LOW: "var(--color-risk-low)",
  MEDIUM: "var(--color-risk-medium)",
  HIGH: "var(--color-risk-high)",
  CRITICAL: "var(--color-risk-critical)",
};

function getNodePos(id: string, fallbackIndex: number, isDepot: boolean): Point {
  if (DEFAULT_POSITIONS[id]) return DEFAULT_POSITIONS[id];
  const normalized = id.toLowerCase();
  for (const [key, pos] of Object.entries(DEFAULT_POSITIONS)) {
    if (normalized.includes(key) || key.includes(normalized)) {
      return pos;
    }
  }
  // Fallback layout
  if (isDepot) {
    return { x: 140, y: 150 + fallbackIndex * 230 };
  }
  return { x: 820, y: 70 + fallbackIndex * 125 };
}

function getWorstRisk(risk: Record<Fuel, { level: RiskLevel }> | undefined): RiskLevel {
  if (!risk) return "LOW";
  const levels = Object.values(risk).map((r) => r?.level);
  if (levels.includes("CRITICAL")) return "CRITICAL";
  if (levels.includes("HIGH")) return "HIGH";
  if (levels.includes("MEDIUM")) return "MEDIUM";
  return "LOW";
}

function formatStationNodeName(name: string, maxLen = 18): string {
  const shortened = name
    .replace(/\s+Fuel\s+Station/i, " Stn")
    .replace(/\s+Highway\s+Station/i, " Hwy")
    .replace(/\s+Regional\s+Station/i, " Reg.")
    .replace(/\s+Industrial\s+Station/i, " Ind.")
    .replace(/\s+Terminal/i, " Term.")
    .replace(/\s+Station/i, " Stn");
  if (shortened.length <= maxLen) return shortened;
  return `${shortened.slice(0, Math.max(0, maxLen - 1)).trimEnd()}…`;
}

function computeCurve(p0: Point, p3: Point, isCrossRegion: boolean) {
  const startX = p0.x + 96;
  const startY = p0.y;
  const endX = p3.x - 96;
  const endY = p3.y;

  if (!isCrossRegion) {
    return {
      d: `M ${startX} ${startY} L ${endX} ${endY}`,
      p0: { x: startX, y: startY },
      p1: { x: startX + (endX - startX) * 0.33, y: startY },
      p2: { x: startX + (endX - startX) * 0.66, y: endY },
      p3: { x: endX, y: endY },
      mid: { x: (startX + endX) / 2, y: (startY + endY) / 2 },
      isCurve: false,
    };
  }

  // Cross-region Bézier curve
  const midX1 = startX + (endX - startX) * 0.4;
  const midY1 = startY + (endY - startY) * 0.1;
  const midX2 = startX + (endX - startX) * 0.6;
  const midY2 = endY - (endY - startY) * 0.1;

  const d = `M ${startX} ${startY} C ${midX1} ${midY1}, ${midX2} ${midY2}, ${endX} ${endY}`;
  // Midpoint at t = 0.5
  const mid = evaluateCubicBezier(
    { x: startX, y: startY },
    { x: midX1, y: midY1 },
    { x: midX2, y: midY2 },
    { x: endX, y: endY },
    0.5
  );

  return {
    d,
    p0: { x: startX, y: startY },
    p1: { x: midX1, y: midY1 },
    p2: { x: midX2, y: midY2 },
    p3: { x: endX, y: endY },
    mid,
    isCurve: true,
  };
}

function evaluateCubicBezier(p0: Point, p1: Point, p2: Point, p3: Point, t: number): Point {
  const mt = 1 - t;
  const x =
    mt * mt * mt * p0.x +
    3 * mt * mt * t * p1.x +
    3 * mt * t * t * p2.x +
    t * t * t * p3.x;
  const y =
    mt * mt * mt * p0.y +
    3 * mt * mt * t * p1.y +
    3 * mt * t * t * p2.y +
    t * t * t * p3.y;
  return { x, y };
}

export function NetworkGraph({ state, onSelectEntity }: NetworkGraphProps) {
  const depots = state?.depots || [];
  const stations = state?.stations || [];
  const routes = state?.routes || [];
  const allocations = state?.allocations_active || [];

  // Map entity positions
  const depotPosMap = new Map<string, Point>();
  depots.forEach((d, idx) => {
    depotPosMap.set(d.id, getNodePos(d.id, idx, true));
  });

  const stationPosMap = new Map<string, Point>();
  stations.forEach((s, idx) => {
    stationPosMap.set(s.id, getNodePos(s.id, idx, false));
  });

  // Calculate route curve geometry
  const routeGeometry = new Map<
    string,
    {
      route: RouteView;
      d: string;
      mid: Point;
      p0: Point;
      p1: Point;
      p2: Point;
      p3: Point;
      isCurve: boolean;
      status: string;
    }
  >();

  routes.forEach((r) => {
    const srcPos = depotPosMap.get(r.source_depot_id);
    const dstPos = stationPosMap.get(r.destination_station_id);
    if (!srcPos || !dstPos) return;

    const isCrossRegion =
      (r.source_depot_id.includes("gazipur") &&
        (r.destination_station_id.includes("karnaphuli") ||
          r.destination_station_id.includes("coxsbazar"))) ||
      (r.source_depot_id.includes("patiya") &&
        (r.destination_station_id.includes("mirpur") ||
          r.destination_station_id.includes("tongi")));

    const geom = computeCurve(srcPos, dstPos, isCrossRegion);
    routeGeometry.set(r.id, {
      route: r,
      d: geom.d,
      mid: geom.mid,
      p0: geom.p0,
      p1: geom.p1,
      p2: geom.p2,
      p3: geom.p3,
      isCurve: geom.isCurve,
      status: r.status,
    });
  });

  return (
    <div
      data-testid="network-graph"
      className="relative flex flex-col rounded-lg border border-bg-border bg-bg-panel p-4 shadow-sm"
    >
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-sm font-medium uppercase tracking-wider text-ink-muted">
          Supply Network Topology
        </h2>
        {/* Legend */}
        <div className="flex flex-wrap items-center gap-4 text-xs text-ink-muted">
          <div className="flex items-center gap-1.5">
            <span className="h-0.5 w-4 bg-bg-border" />
            <span>Available</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="h-0.5 w-4 bg-risk-critical" />
            <span className="text-risk-critical">Disrupted</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="h-0.5 w-4 border-t-2 border-dashed border-risk-medium" />
            <span className="text-risk-medium">Scheduled</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full bg-fuel-diesel animate-pulse" />
            <span>In Transit</span>
          </div>
        </div>
      </div>

      <div className="w-full overflow-hidden">
        <svg
          viewBox="0 0 1000 520"
          className="h-auto w-full select-none"
          preserveAspectRatio="xMidYMid meet"
        >
          <defs>
            {/* Soft glow for in-transit dots */}
            <filter id="dot-glow" x="-50%" y="-50%" width="200%" height="200%">
              <feGaussianBlur in="SourceGraphic" stdDeviation="2" result="blur" />
              <feMerge>
                <feMergeNode in="blur" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
          </defs>

          {/* 1. Routes (Edges) */}
          <g className="routes-layer">
            {Array.from(routeGeometry.values()).map((rg) => {
              const isDisrupted = rg.status === "DISRUPTED";
              const isScheduled =
                rg.status === "SCHEDULED_DISRUPTION" ||
                Boolean(rg.route.scheduled_disruption);

              let strokeColor = "var(--color-bg-border)";
              let strokeWidth = 2;
              let strokeDasharray = "none";

              if (isDisrupted) {
                strokeColor = "var(--color-risk-critical)";
                strokeWidth = 3;
              } else if (isScheduled) {
                strokeColor = "var(--color-risk-medium)";
                strokeWidth = 2.5;
                strokeDasharray = "6 4";
              }

              return (
                <g key={rg.route.id} data-testid={`edge-${rg.route.id}`}>
                  {/* Route edge line/curve */}
                  <path
                    d={rg.d}
                    fill="none"
                    stroke={strokeColor}
                    strokeWidth={strokeWidth}
                    strokeDasharray={strokeDasharray}
                    strokeLinecap="round"
                    className="transition-all duration-300"
                  >
                    <title>{`${rg.route.id} (${rg.status}) · transit: ${rg.route.transit_ticks}t · max: ${liters(rg.route.max_shipment)}`}</title>
                  </path>

                  {/* Transit ticks badge at midpoint */}
                  <g transform={`translate(${rg.mid.x}, ${rg.mid.y})`}>
                    <rect
                      x="-14"
                      y="-10"
                      width="28"
                      height="20"
                      rx="4"
                      fill="var(--color-bg-panel)"
                      stroke={strokeColor}
                      strokeWidth="1"
                    />
                    <text
                      x="0"
                      y="4"
                      textAnchor="middle"
                      className="fill-ink text-[10px] font-semibold font-mono"
                    >
                      {rg.route.transit_ticks}t
                    </text>
                  </g>
                </g>
              );
            })}
          </g>

          {/* 2. In-transit Allocation Dots */}
          <g className="allocations-layer">
            {allocations.map((alloc) => {
              const rg = routeGeometry.get(alloc.route_id);
              if (!rg) return null;

              const t = Math.max(0.0, Math.min(1.0, alloc.progress ?? 0));
              const pos = rg.isCurve
                ? evaluateCubicBezier(rg.p0, rg.p1, rg.p2, rg.p3, t)
                : {
                    x: rg.p0.x + (rg.p3.x - rg.p0.x) * t,
                    y: rg.p0.y + (rg.p3.y - rg.p0.y) * t,
                  };

              const fuelColor = FUEL_COLORS[alloc.fuel_type] || "var(--color-fuel-diesel)";

              return (
                <g
                  key={alloc.sim_allocation_id}
                  data-testid={`allocation-${alloc.sim_allocation_id}`}
                  transform={`translate(${pos.x}, ${pos.y})`}
                >
                  <circle
                    r="6"
                    fill={fuelColor}
                    stroke="var(--color-bg-panel)"
                    strokeWidth="1.5"
                    filter="url(#dot-glow)"
                    className="cursor-pointer transition-transform duration-200 hover:scale-125"
                  >
                    <title>{`Allocation #${alloc.sim_allocation_id}: ${liters(alloc.quantity)} ${alloc.fuel_type} (ETA t=${alloc.expected_arrival_tick ?? "—"})`}</title>
                  </circle>
                </g>
              );
            })}
          </g>

          {/* 3. Depots Nodes */}
          <g className="depots-layer">
            {depots.map((depot) => {
              const pos = depotPosMap.get(depot.id);
              if (!pos) return null;

              const isConstrained = depot.status === "CONSTRAINED";
              const strokeColor = isConstrained
                ? "var(--color-risk-medium)"
                : "var(--color-bg-border)";

              return (
                <g
                  key={depot.id}
                  data-testid={`node-${depot.id}`}
                  transform={`translate(${pos.x - 82}, ${pos.y - 28})`}
                  onClick={() => onSelectEntity?.(depot)}
                  className="cursor-pointer group"
                >
                  <rect
                    width="164"
                    height="56"
                    rx="8"
                    fill="var(--color-bg-raised)"
                    stroke={strokeColor}
                    strokeWidth="2"
                    className="transition group-hover:stroke-brand"
                  >
                    <title>{`${depot.name} (${depot.status})`}</title>
                  </rect>

                  {/* Header text inside node */}
                  <text
                    x="10"
                    y="18"
                    className="fill-ink text-xs font-semibold tracking-tight"
                  >
                    {depot.name}
                  </text>

                  <rect
                    x="116"
                    y="6"
                    width="40"
                    height="14"
                    rx="3"
                    fill={isConstrained ? "color-mix(in srgb, var(--color-risk-medium) 15%, transparent)" : "color-mix(in srgb, var(--color-risk-low) 12%, transparent)"}
                    stroke={isConstrained ? "var(--color-risk-medium)" : "var(--color-risk-low)"}
                    strokeWidth="0.8"
                  />
                  <text
                    x="136"
                    y="16.5"
                    textAnchor="middle"
                    fill={isConstrained ? "var(--color-risk-medium)" : "var(--color-risk-low)"}
                    className="text-[9px] font-bold tracking-wider font-mono uppercase"
                  >
                    {depot.status}
                  </text>

                  {/* 3 Fuel Fill Bars */}
                  {(["DIESEL", "PETROL", "OCTANE"] as Fuel[]).map((fuel, fIdx) => {
                    const cap = depot.capacity?.[fuel] || 1;
                    const inv = depot.inventory?.[fuel] || 0;
                    const fillPct = Math.max(0, Math.min(1, inv / cap));
                    const barWidth = 42;
                    const barX = 10 + fIdx * 50;

                    return (
                      <g key={fuel} transform={`translate(${barX}, 28)`}>
                        <rect
                          width={barWidth}
                          height="8"
                          rx="2"
                          fill="var(--color-bg)"
                        />
                        <rect
                          width={barWidth * fillPct}
                          height="8"
                          rx="2"
                          fill={FUEL_COLORS[fuel]}
                        />
                        <text
                          x="0"
                          y="18"
                          className="fill-ink-muted text-[9px] font-mono font-medium"
                        >
                          {fuel[0]} {Math.round(fillPct * 100)}%
                        </text>
                      </g>
                    );
                  })}
                </g>
              );
            })}
          </g>

          {/* 4. Stations Nodes */}
          <g className="stations-layer">
            {stations.map((station) => {
              const pos = stationPosMap.get(station.id);
              if (!pos) return null;

              const worstRisk = getWorstRisk(station.risk);
              const strokeColor = RISK_BORDER_COLORS[worstRisk];

              return (
                <g
                  key={station.id}
                  data-testid={`node-${station.id}`}
                  transform={`translate(${pos.x - 96}, ${pos.y - 28})`}
                  onClick={() => onSelectEntity?.(station)}
                  className="cursor-pointer group"
                >
                  <rect
                    width="192"
                    height="56"
                    rx="8"
                    fill="var(--color-bg-raised)"
                    stroke={strokeColor}
                    strokeWidth={worstRisk === "HIGH" || worstRisk === "CRITICAL" ? 2.5 : 1.5}
                    className="transition group-hover:stroke-brand"
                  >
                    <title>{`${station.name} (${station.status}) · Risk: ${worstRisk}`}</title>
                  </rect>

                  {/* Header text — clipped so it never overlaps the risk badge */}
                  <text
                    x="10"
                    y="18"
                    className="fill-ink text-xs font-semibold tracking-tight"
                  >
                    {formatStationNodeName(station.name, 18)}
                  </text>

                  {/* Risk Badge on Node (right edge of wider card) */}
                  <rect
                    x="148"
                    y="6"
                    width="36"
                    height="14"
                    rx="3"
                    fill={`${strokeColor}25`}
                    stroke={strokeColor}
                    strokeWidth="0.8"
                  />
                  <text
                    x="166"
                    y="16.5"
                    textAnchor="middle"
                    fill={strokeColor}
                    className="text-[9px] font-bold tracking-wider font-mono uppercase"
                  >
                    {worstRisk === "CRITICAL" ? "CRIT" : worstRisk}
                  </text>

                  {/* 3 Fuel Fill Bars */}
                  {(["DIESEL", "PETROL", "OCTANE"] as Fuel[]).map((fuel, fIdx) => {
                    const fillPct = station.fill_pct?.[fuel] ?? 0;
                    const clampedFill = Math.max(0, Math.min(1, fillPct));
                    const barWidth = 42;
                    const barX = 10 + fIdx * 50;

                    return (
                      <g key={fuel} transform={`translate(${barX}, 28)`}>
                        <rect
                          width={barWidth}
                          height="8"
                          rx="2"
                          fill="var(--color-bg)"
                        />
                        <rect
                          width={barWidth * clampedFill}
                          height="8"
                          rx="2"
                          fill={FUEL_COLORS[fuel]}
                        />
                        <text
                          x="0"
                          y="18"
                          className="fill-ink-muted text-[9px] font-mono font-medium"
                        >
                          {fuel[0]} {Math.round(clampedFill * 100)}%
                        </text>
                      </g>
                    );
                  })}
                </g>
              );
            })}
          </g>
        </svg>
      </div>
    </div>
  );
}
