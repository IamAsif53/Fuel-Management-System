"""Break-glass Static Emergency Plan Generator (Phase 10: F-BREAKGLASS).

Generates static HTML and CSV emergency plans every 4 ticks into breakglass_dir (/data/breakglass).
These files can be served directly by Nginx (at /breakglass/latest.html) or retrieved
by operators even when the API, Database, Engine, or React frontend are completely down.

Specifications:
- 06_RESILIENCE_AND_OBSERVABILITY.md
- 20_FINAL_SPEC_LOCK.md §F-BREAKGLASS
- 21_API_CONTRACTS.md §GET /api/breakglass/plan.csv
"""

from __future__ import annotations

import csv
import os
import tempfile
from datetime import UTC, datetime
from typing import Any

from fsip_common.logging import get_logger

log = get_logger()


class BreakglassWriter:
    """Manages periodic emission of static emergency dispatch plans and cover tables."""

    def __init__(self, breakglass_dir: str = "/data/breakglass") -> None:
        self.breakglass_dir = breakglass_dir
        self.last_written_tick: int = -1

    def _ensure_dir(self) -> str:
        """Ensure destination directory exists or fallback to local directory."""
        target_dir = self.breakglass_dir
        try:
            os.makedirs(target_dir, exist_ok=True)
            # Test write access
            test_file = os.path.join(target_dir, ".test_write")
            with open(test_file, "w") as f:
                f.write("test")
            os.remove(test_file)
            return target_dir
        except Exception:
            # Fallback to system temp directory
            fallback = os.path.join(tempfile.gettempdir(), "breakglass_data")
            try:
                os.makedirs(fallback, exist_ok=True)
                return fallback
            except Exception:
                return target_dir

    def write(
        self,
        store: Any,
        plan_lines: list[Any] | None = None,
        force: bool = False,
    ) -> tuple[str | None, str | None]:
        """Write latest.csv and latest.html atomically.

        Emits if tick % 4 == 0 or if force=True.
        Returns paths (csv_path, html_path) or (None, None) if skipped.
        """
        cur_tick = getattr(store, "tick", 0)
        if not force and cur_tick % 4 != 0 and self.last_written_tick >= 0:
            return None, None

        target_dir = self._ensure_dir()
        csv_tmp = os.path.join(target_dir, "latest.csv.tmp")
        csv_path = os.path.join(target_dir, "latest.csv")
        html_tmp = os.path.join(target_dir, "latest.html.tmp")
        html_path = os.path.join(target_dir, "latest.html")

        now_iso = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        sim_run_id = getattr(store, "sim_run_id", 1)
        engine_mode = getattr(store, "engine_mode", "NORMAL")

        # 1. Generate CSV
        try:
            with open(csv_tmp, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["depot", "station", "route", "fuel", "quantity", "reason", "generated_tick"])
                if plan_lines:
                    for line in plan_lines:
                        depot = (
                            getattr(line, "source_depot_id", None)
                            or getattr(line, "depot_id", "")
                            or (line.get("source_depot_id", "") if isinstance(line, dict) else "")
                        )
                        station = (
                            getattr(line, "destination_station_id", None)
                            or getattr(line, "station_id", "")
                            or (line.get("destination_station_id", "") if isinstance(line, dict) else "")
                        )
                        route = (
                            getattr(line, "route_id", "")
                            or (line.get("route_id", "") if isinstance(line, dict) else "")
                        )
                        fuel = (
                            getattr(line, "fuel_type", "")
                            or (line.get("fuel_type", "") if isinstance(line, dict) else "")
                        )
                        qty = (
                            getattr(line, "quantity", 0.0)
                            or (line.get("quantity", 0.0) if isinstance(line, dict) else 0.0)
                        )
                        reason = (
                            getattr(line, "reason", "")
                            or (line.get("reason", "") if isinstance(line, dict) else "")
                        )
                        writer.writerow([depot, station, route, fuel, round(float(qty), 1), reason, cur_tick])
            os.replace(csv_tmp, csv_path)
        except Exception as exc:
            log.warning("breakglass.csv_write_failed", error=str(exc))

        # 2. Generate HTML
        try:
            html_content = self._build_html(
                store=store,
                plan_lines=plan_lines,
                cur_tick=cur_tick,
                sim_run_id=sim_run_id,
                now_iso=now_iso,
                engine_mode=engine_mode,
            )
            with open(html_tmp, "w", encoding="utf-8") as f:
                f.write(html_content)
            os.replace(html_tmp, html_path)
            self.last_written_tick = cur_tick
            log.info("breakglass.written", tick=cur_tick, dir=target_dir)
            return csv_path, html_path
        except Exception as exc:
            log.warning("breakglass.html_write_failed", error=str(exc))
            return None, None

    def _build_html(
        self,
        store: Any,
        plan_lines: list[Any] | None,
        cur_tick: int,
        sim_run_id: int,
        now_iso: str,
        engine_mode: str,
    ) -> str:
        """Construct static standalone HTML report with CSS and emergency tables."""
        # Plan lines rows
        plan_rows = ""
        if plan_lines:
            for ln in plan_lines:
                depot = (
                    getattr(ln, "source_depot_id", None)
                    or (ln.get("source_depot_id", "") if isinstance(ln, dict) else "")
                )
                station = (
                    getattr(ln, "destination_station_id", None)
                    or (ln.get("destination_station_id", "") if isinstance(ln, dict) else "")
                )
                route = getattr(ln, "route_id", "") or (ln.get("route_id", "") if isinstance(ln, dict) else "")
                fuel = getattr(ln, "fuel_type", "") or (ln.get("fuel_type", "") if isinstance(ln, dict) else "")
                qty = float(getattr(ln, "quantity", 0.0) or (ln.get("quantity", 0.0) if isinstance(ln, dict) else 0.0))
                reason = getattr(ln, "reason", "") or (ln.get("reason", "") if isinstance(ln, dict) else "")
                plan_rows += f"""<tr>
                    <td><strong>{depot}</strong></td>
                    <td><strong>{station}</strong></td>
                    <td>{route}</td>
                    <td><span class="badge badge-{fuel.lower()}">{fuel}</span></td>
                    <td class="num">{qty:,.0f} L</td>
                    <td>{reason}</td>
                </tr>"""
        else:
            plan_rows = (
                "<tr><td colspan='6' class='text-muted'>"
                "No active emergency dispatch recommendations at this tick.</td></tr>"
            )

        # Station Cover table rows
        station_rows = ""
        stations = getattr(store, "stations", {})
        st_items = stations.values() if isinstance(stations, dict) else stations
        risk_map = getattr(store, "risk", {})

        for st in st_items:
            sid = getattr(st, "id", "") or (st.get("id") if isinstance(st, dict) else "")
            reg = getattr(st, "region_id", "") or (st.get("region_id") if isinstance(st, dict) else "")
            inv_map = getattr(st, "inventory", {}) if hasattr(st, "inventory") else st.get("inventory", {})
            cap_map = getattr(st, "capacity", {}) if hasattr(st, "capacity") else st.get("capacity", {})

            for fuel in ("DIESEL", "PETROL", "OCTANE"):
                inv = float(inv_map.get(fuel, 0.0)) if isinstance(inv_map, dict) else 0.0
                cap = float(cap_map.get(fuel, 10000.0)) if isinstance(cap_map, dict) else 10000.0
                fill_pct = (inv / cap * 100.0) if cap > 0 else 0.0

                # Cover / risk estimation
                r_info = risk_map.get(sid, {}).get(fuel, {}) if isinstance(risk_map, dict) else {}
                cover_h = r_info.get("hours_to_stockout", "—")
                p_lead = r_info.get("p_lead", "—")
                risk_lvl = r_info.get("risk_level", "LOW")

                badge_cls = "danger" if risk_lvl == "CRITICAL" else ("warning" if risk_lvl == "HIGH" else "info")
                station_rows += f"""<tr>
                    <td>{sid}</td>
                    <td>{reg}</td>
                    <td>{fuel}</td>
                    <td class="num">{inv:,.0f} L</td>
                    <td class="num">{cap:,.0f} L</td>
                    <td class="num">{fill_pct:.1f}%</td>
                    <td class="num">{cover_h}</td>
                    <td><span class="badge badge-{badge_cls}">{risk_lvl} ({p_lead})</span></td>
                </tr>"""

        # Depot Inventory rows
        depot_rows = ""
        depots = getattr(store, "depots", {})
        d_items = depots.values() if isinstance(depots, dict) else depots
        for d in d_items:
            did = getattr(d, "id", "") or (d.get("id") if isinstance(d, dict) else "")
            reg = getattr(d, "region_id", "") or (d.get("region_id") if isinstance(d, dict) else "")
            inv_map = getattr(d, "inventory", {}) if hasattr(d, "inventory") else d.get("inventory", {})
            cap_map = getattr(d, "capacity", {}) if hasattr(d, "capacity") else d.get("capacity", {})

            for fuel in ("DIESEL", "PETROL", "OCTANE"):
                inv = float(inv_map.get(fuel, 0.0)) if isinstance(inv_map, dict) else 0.0
                cap = float(cap_map.get(fuel, 100000.0)) if isinstance(cap_map, dict) else 100000.0
                fill_pct = (inv / cap * 100.0) if cap > 0 else 0.0
                depot_rows += f"""<tr>
                    <td>{did}</td>
                    <td>{reg}</td>
                    <td>{fuel}</td>
                    <td class="num">{inv:,.0f} L</td>
                    <td class="num">{cap:,.0f} L</td>
                    <td class="num">{fill_pct:.1f}%</td>
                </tr>"""

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FSIP Break-Glass Emergency Plan</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background: #0f172a;
            color: #f8fafc;
            margin: 0;
            padding: 24px;
        }}
        .header {{
            background: #1e293b;
            padding: 20px;
            border-radius: 8px;
            margin-bottom: 24px;
            border-left: 6px solid #ef4444;
        }}
        h1 {{ margin: 0 0 8px 0; font-size: 24px; color: #f8fafc; }}
        .meta {{ font-size: 14px; color: #94a3b8; display: flex; gap: 20px; flex-wrap: wrap; }}
        .card {{
            background: #1e293b;
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 24px;
            border: 1px solid #334155;
        }}
        h2 {{ margin-top: 0; font-size: 18px; color: #38bdf8; border-bottom: 1px solid #334155; padding-bottom: 10px; }}
        table {{ width: 100%; border-collapse: collapse; font-size: 14px; margin-top: 10px; }}
        th, td {{ padding: 10px 12px; text-align: left; border-bottom: 1px solid #334155; }}
        th {{ background: #0f172a; color: #94a3b8; font-weight: 600; text-transform: uppercase; font-size: 12px; }}
        td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
        .badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 700; }}
        .badge-danger {{ background: #ef4444; color: #fff; }}
        .badge-warning {{ background: #f59e0b; color: #000; }}
        .badge-info {{ background: #0284c7; color: #fff; }}
        .badge-diesel {{ background: #0d9488; color: #fff; }}
        .badge-petrol {{ background: #d97706; color: #fff; }}
        .badge-octane {{ background: #9333ea; color: #fff; }}
        .notice {{
            background: #451a03;
            border: 1px solid #b45309;
            padding: 12px;
            border-radius: 6px;
            color: #fde68a;
            font-size: 13px;
            margin-bottom: 20px;
        }}
        .text-muted {{ color: #64748b; text-align: center; padding: 20px; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>🚨 FSIP Emergency Break-Glass Dispatch & Station Cover Plan</h1>
        <div class="meta">
            <span><strong>Generated:</strong> {now_iso}</span>
            <span><strong>Sim Run:</strong> #{sim_run_id}</span>
            <span><strong>Tick:</strong> {cur_tick}</span>
            <span><strong>Engine Mode:</strong> {engine_mode}</span>
        </div>
    </div>

    <div class="notice">
        <strong>⚠️ OPERATIONAL NOTICE:</strong> This static emergency dashboard was compiled
        directly by the FSIP decision engine into local static storage. It requires zero live database
        connections, API servers, or external services. If network or platform services fail,
        use the dispatch table below for manual operational routing.
    </div>

    <div class="card">
        <h2>Recommended Emergency Dispatch Allocations</h2>
        <table>
            <thead>
                <tr>
                    <th>Source Depot</th>
                    <th>Destination Station</th>
                    <th>Route</th>
                    <th>Fuel</th>
                    <th style="text-align: right;">Quantity</th>
                    <th>Reason</th>
                </tr>
            </thead>
            <tbody>
                {plan_rows}
            </tbody>
        </table>
    </div>

    <div class="card">
        <h2>Station Cover & Stockout Risk</h2>
        <table>
            <thead>
                <tr>
                    <th>Station</th>
                    <th>Region</th>
                    <th>Fuel</th>
                    <th style="text-align: right;">Current Inv</th>
                    <th style="text-align: right;">Capacity</th>
                    <th style="text-align: right;">Fill %</th>
                    <th style="text-align: right;">Est Cover (Hrs)</th>
                    <th>Risk Level</th>
                </tr>
            </thead>
            <tbody>
                {station_rows}
            </tbody>
        </table>
    </div>

    <div class="card">
        <h2>Depot Bulk Inventory</h2>
        <table>
            <thead>
                <tr>
                    <th>Depot</th>
                    <th>Region</th>
                    <th>Fuel</th>
                    <th style="text-align: right;">Current Inv</th>
                    <th style="text-align: right;">Capacity</th>
                    <th style="text-align: right;">Fill %</th>
                </tr>
            </thead>
            <tbody>
                {depot_rows}
            </tbody>
        </table>
    </div>
</body>
</html>"""
