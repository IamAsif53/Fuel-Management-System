"""Inventory Anomaly Detection and Reconciliation (F-RECON) Module (Task 7.3).

Per 12_ADVANCED_FEATURES.md (F-RECON), 05_INTELLIGENCE_DESIGN.md §3, & 21_API_CONTRACTS.md:
- Station balance: expected Δinventory = arrivals - served.
  Residual > max(50 L, 5% of capacity) -> INVENTORY_ANOMALY.
- Station variance CUSUM: variance = Δinventory - (arrivals - served)
  CUSUM with k=25 L, h=300 L -> INVENTORY_VARIANCE alert.
- Depot balance: Δinventory = supply_arrived - allocations_created.
- Allocation reconciliation: sent vs received.
  FAILED -> 100% loss. Realized arrival clipping -> OVERFLOW loss.
- Report generator for GET /api/reports/reconciliation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fsip_common.logging import get_logger
from prometheus_client import REGISTRY, Counter
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.manager import AlertSpec
from app.db.models import AllocationReconciliation

logger = get_logger()

# ---------------------------------------------------------------- Prometheus Metrics
try:
    FSIP_RECON_LOSS_LITERS_TOTAL = Counter(
        "fsip_recon_loss_liters_total",
        "Total fuel loss liters detected during reconciliation",
        ["loss_reason"],
    )
except ValueError:
    FSIP_RECON_LOSS_LITERS_TOTAL = REGISTRY._names_to_collectors.get(  # type: ignore[assignment]
        "fsip_recon_loss_liters_total"
    )


@dataclass
class ReconciliationLine:
    """Reconciliation record for an allocation."""

    line_id: int
    sent: float
    received: float
    loss: float
    loss_reason: str  # NONE | FAILED_ROUTE | OVERFLOW
    method: str = "audit"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    tick: int = 0


@dataclass
class StationVarianceStatus:
    """Station variance and CUSUM status."""

    station_id: str
    fuel_type: str
    variance_cusum: float
    alarm: bool


class StationBalanceTracker:
    """Tracks station inventory deltas against arrivals and served demand."""

    def __init__(self, k: float = 25.0, h: float = 300.0) -> None:
        self.k = k
        self.h = h
        self.prev_inventory: dict[tuple[str, str], float] = {}
        self.cusum_pos: dict[tuple[str, str], float] = {}
        self.cusum_neg: dict[tuple[str, str], float] = {}

    def reset(self) -> None:
        """Reset internal inventory memory and CUSUMs."""
        self.prev_inventory.clear()
        self.cusum_pos.clear()
        self.cusum_neg.clear()

    def update(
        self,
        station_id: str,
        fuel_type: str,
        current_inventory: float,
        capacity: float,
        arrivals_liters: float,
        served_liters: float,
        tick: int,
    ) -> tuple[AlertSpec | None, AlertSpec | None]:
        """Update inventory balance for station and fuel.

        Returns (inventory_anomaly_alert, inventory_variance_alert).
        """
        key = (station_id, fuel_type)
        prev_inv = self.prev_inventory.get(key)
        self.prev_inventory[key] = current_inventory

        if prev_inv is None:
            # First tick observation; baseline established
            return None, None

        actual_delta = current_inventory - prev_inv
        expected_delta = arrivals_liters - served_liters
        residual = abs(actual_delta - expected_delta)
        variance = actual_delta - expected_delta

        # 1. Point inventory anomaly: residual > max(50 L, 5% of capacity)
        anomaly_threshold = max(50.0, 0.05 * capacity)
        anomaly_alert: AlertSpec | None = None
        if residual > anomaly_threshold:
            anomaly_alert = AlertSpec(
                fingerprint=f"station:{station_id}:{fuel_type}:inventory_anomaly",
                category="INVENTORY_ANOMALY",
                severity="WARNING",
                title=f"Inventory Anomaly at {station_id}",
                message=(
                    f"Station {station_id} {fuel_type} inventory Δ={actual_delta:+.1f} L "
                    f"diverges from expected {expected_delta:+.1f} L (residual {residual:.1f} L "
                    f"> {anomaly_threshold:.1f} L). Possible meter error, leak, or data inconsistency."
                ),
                entity_type="station",
                entity_id=station_id,
                fuel_type=fuel_type,
                opened_tick=tick,
                data={
                    "actual_delta": actual_delta,
                    "expected_delta": expected_delta,
                    "residual": residual,
                    "threshold": anomaly_threshold,
                },
            )

        # 2. Cumulative variance CUSUM (k=25, h=300)
        c_pos = max(0.0, self.cusum_pos.get(key, 0.0) + variance - self.k)
        c_neg = max(0.0, self.cusum_neg.get(key, 0.0) - variance - self.k)
        self.cusum_pos[key] = c_pos
        self.cusum_neg[key] = c_neg

        is_alarm = (c_pos > self.h) or (c_neg > self.h)
        cusum_val = c_pos if c_pos > self.h else c_neg

        variance_alert: AlertSpec | None = None
        if is_alarm:
            variance_alert = AlertSpec(
                fingerprint=f"station:{station_id}:{fuel_type}:inventory_variance",
                category="INVENTORY_VARIANCE",
                severity="WARNING",
                title=f"Inventory Variance Alarm at {station_id}",
                message=(
                    f"Station {station_id} {fuel_type} inventory variance CUSUM {cusum_val:.1f} L "
                    f"exceeds {self.h:.1f} L threshold. Persistent unmetered loss or gain detected."
                ),
                entity_type="station",
                entity_id=station_id,
                fuel_type=fuel_type,
                opened_tick=tick,
                data={
                    "cusum_val": cusum_val,
                    "threshold": self.h,
                    "variance": variance,
                },
            )

        return anomaly_alert, variance_alert

    def get_station_cusums(self) -> list[StationVarianceStatus]:
        """Return list of all station variance CUSUM values and alarm states."""
        results: list[StationVarianceStatus] = []
        all_keys = set(self.cusum_pos.keys()).union(self.cusum_neg.keys())
        for st_id, f_type in sorted(all_keys):
            c_pos = self.cusum_pos.get((st_id, f_type), 0.0)
            c_neg = self.cusum_neg.get((st_id, f_type), 0.0)
            c_val = max(c_pos, c_neg)
            results.append(
                StationVarianceStatus(
                    station_id=st_id,
                    fuel_type=f_type,
                    variance_cusum=round(c_val, 2),
                    alarm=c_val > self.h,
                )
            )
        return results


class DepotBalanceTracker:
    """Tracks depot inventory deltas against supply arrivals and dispatched allocations."""

    def __init__(self) -> None:
        self.prev_inventory: dict[tuple[str, str], float] = {}

    def reset(self) -> None:
        self.prev_inventory.clear()

    def update(
        self,
        depot_id: str,
        fuel_type: str,
        current_inventory: float,
        capacity: float,
        supply_arrived_liters: float,
        allocations_created_liters: float,
        tick: int,
    ) -> AlertSpec | None:
        key = (depot_id, fuel_type)
        prev = self.prev_inventory.get(key)
        self.prev_inventory[key] = current_inventory

        if prev is None:
            return None

        actual_delta = current_inventory - prev
        expected_delta = supply_arrived_liters - allocations_created_liters
        residual = abs(actual_delta - expected_delta)
        threshold = max(100.0, 0.05 * capacity)

        if residual > threshold:
            return AlertSpec(
                fingerprint=f"depot:{depot_id}:{fuel_type}:inventory_anomaly",
                category="INVENTORY_ANOMALY",
                severity="WARNING",
                title=f"Depot Inventory Anomaly at {depot_id}",
                message=(
                    f"Depot {depot_id} {fuel_type} Δ={actual_delta:+.1f} L "
                    f"differs from expected {expected_delta:+.1f} L by {residual:.1f} L (> {threshold:.1f} L)."
                ),
                entity_type="depot",
                entity_id=depot_id,
                fuel_type=fuel_type,
                opened_tick=tick,
                data={
                    "actual_delta": actual_delta,
                    "expected_delta": expected_delta,
                    "residual": residual,
                },
            )
        return None


class AllocationReconciliationTracker:
    """Reconciles dispatched allocations against realized arrivals and computes loss rates."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.reconciled_lines: dict[int, ReconciliationLine] = {}
        self.processed_allocation_ids: set[int] = set()

    def reset(self) -> None:
        self.reconciled_lines.clear()
        self.processed_allocation_ids.clear()

    async def record_allocation(
        self,
        line_id: int,
        sent: float,
        status: str,
        station_capacity: float,
        station_inventory_before_arrival: float,
        tick: int,
    ) -> ReconciliationLine:
        """Reconcile an allocation once reached terminal state (ARRIVED, DELIVERED, or FAILED)."""
        if line_id in self.reconciled_lines:
            return self.reconciled_lines[line_id]

        sent = float(sent)
        received = sent
        loss = 0.0
        reason = "NONE"

        if status.upper() in ("FAILED", "CANCELLED", "REJECTED"):
            received = 0.0
            loss = sent
            reason = "FAILED_ROUTE"
        else:
            # Check overflow
            room = max(0.0, station_capacity - station_inventory_before_arrival)
            if sent > room:
                received = room
                loss = sent - room
                reason = "OVERFLOW"

        if loss > 0 and FSIP_RECON_LOSS_LITERS_TOTAL is not None:
            FSIP_RECON_LOSS_LITERS_TOTAL.labels(loss_reason=reason).inc(loss)

        line = ReconciliationLine(
            line_id=line_id,
            sent=sent,
            received=received,
            loss=loss,
            loss_reason=reason,
            method="audit",
            tick=tick,
        )
        self.reconciled_lines[line_id] = line
        self.processed_allocation_ids.add(line_id)

        # Persist to database if session factory provided
        if self.session_factory is not None:
            try:
                async with self.session_factory() as session:
                    db_row = AllocationReconciliation(
                        decision_line_id=line_id,
                        sent=sent,
                        received=received,
                        loss=loss,
                        loss_reason=reason,
                        method="audit",
                    )
                    session.add(db_row)
                    await session.commit()
            except Exception as exc:
                logger.error(
                    "recon.db_persist_failed",
                    line_id=line_id,
                    error=str(exc),
                )

        return line

    def get_report(
        self,
        from_tick: int | None = None,
        to_tick: int | None = None,
        station_cusums: list[StationVarianceStatus] | None = None,
    ) -> dict[str, Any]:
        """Generate structured reconciliation report matching GET /api/reports/reconciliation."""
        filtered_lines: list[ReconciliationLine] = []
        for line in self.reconciled_lines.values():
            if from_tick is not None and line.tick < from_tick:
                continue
            if to_tick is not None and line.tick > to_tick:
                continue
            filtered_lines.append(line)

        total_sent = sum(item.sent for item in filtered_lines)
        total_received = sum(item.received for item in filtered_lines)
        total_loss = sum(item.loss for item in filtered_lines)
        loss_rate = round((total_loss / max(1.0, total_sent)) * 100, 2)

        stations_output = [
            {
                "station_id": s.station_id,
                "fuel_type": s.fuel_type,
                "variance_cusum": s.variance_cusum,
                "alarm": s.alarm,
            }
            for s in (station_cusums or [])
        ]

        return {
            "lines": [
                {
                    "line_id": item.line_id,
                    "sent": item.sent,
                    "received": item.received,
                    "loss": item.loss,
                    "loss_reason": item.loss_reason,
                    "method": item.method,
                }
                for item in filtered_lines
            ],
            "totals": {
                "sent": total_sent,
                "received": total_received,
                "loss": total_loss,
                "loss_rate": loss_rate,
            },
            "stations": stations_output,
        }
