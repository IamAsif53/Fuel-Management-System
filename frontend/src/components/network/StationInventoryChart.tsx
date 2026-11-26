import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  ReferenceLine,
} from "recharts";
import type { StationHistoryEntry, FuelMap } from "@/api/types";
import { liters } from "@/lib/format";

interface StationInventoryChartProps {
  history: StationHistoryEntry[];
  capacity?: FuelMap;
}

export function StationInventoryChart({ history, capacity }: StationInventoryChartProps) {
  if (!history || history.length === 0) {
    return (
      <div className="flex h-36 items-center justify-center rounded border border-panel-border/40 bg-surface/40 text-xs text-ink-muted">
        No historical inventory recorded yet
      </div>
    );
  }

  // Format data for Recharts
  const chartData = history.map((entry) => ({
    tick: entry.tick,
    DIESEL: entry.inventory?.DIESEL ?? 0,
    PETROL: entry.inventory?.PETROL ?? 0,
    OCTANE: entry.inventory?.OCTANE ?? 0,
  }));

  const maxCap = capacity
    ? Math.max(capacity.DIESEL || 0, capacity.PETROL || 0, capacity.OCTANE || 0)
    : undefined;

  return (
    <div className="h-44 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={chartData} margin={{ top: 8, right: 12, left: -20, bottom: 0 }}>
          <XAxis
            dataKey="tick"
            stroke="#64748B"
            tick={{ fontSize: 10, fill: "#64748B" }}
            tickLine={false}
          />
          <YAxis
            stroke="#64748B"
            tick={{ fontSize: 10, fill: "#64748B" }}
            tickLine={false}
            tickFormatter={(val: number) => (val >= 1000 ? `${(val / 1000).toFixed(0)}k` : `${val}`)}
            domain={maxCap ? [0, Math.ceil(maxCap * 1.05)] : [0, "auto"]}
          />
          <Tooltip
            contentStyle={{
              backgroundColor: "var(--color-bg-panel)",
              borderColor: "#24314F",
              borderRadius: "4px",
              fontSize: "11px",
              color: "#F8FAFC",
            }}
            formatter={(value: number, name: string) => [liters(value), name]}
            labelFormatter={(label) => `Tick ${label}`}
          />
          <Legend
            verticalAlign="top"
            height={20}
            iconType="circle"
            wrapperStyle={{ fontSize: "10px", paddingBottom: "4px" }}
          />
          {maxCap && (
            <ReferenceLine
              y={maxCap}
              stroke="#64748B"
              strokeDasharray="3 3"
              label={{
                value: "Max Cap",
                fill: "#64748B",
                fontSize: 9,
                position: "insideTopRight",
              }}
            />
          )}
          <ReferenceLine y={0} stroke="#EF4444" strokeWidth={1} strokeDasharray="2 2" />
          <Line
            type="monotone"
            dataKey="DIESEL"
            name="Diesel"
            stroke="#38BDF8"
            strokeWidth={1.5}
            dot={false}
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="PETROL"
            name="Petrol"
            stroke="#A78BFA"
            strokeWidth={1.5}
            dot={false}
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="OCTANE"
            name="Octane"
            stroke="#F472B6"
            strokeWidth={1.5}
            dot={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
