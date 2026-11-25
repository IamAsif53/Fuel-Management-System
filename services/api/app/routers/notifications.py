"""Phase 19: Notification Escalation API Router (/api/notifications)."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.services.notifier import NotificationService, get_notification_history

router = APIRouter(prefix="/api/notifications", tags=["notifications"])

_service = NotificationService()


class NotificationRequest(BaseModel):
    alert_id: str | int = Field(default="P1-TEST-001")
    title: str = Field(..., description="Alert headline")
    severity: str = Field(default="CRITICAL", description="CRITICAL, HIGH, or WARNING")
    entity_id: str = Field(..., description="Target station or depot ID")
    causal_driver: str = Field(..., description="Causal explanation")
    recommended_action: str = Field(..., description="Prescribed mitigation action")
    tick: int | None = Field(default=None)
    recipient: str | None = Field(default=None)


@router.post("/test")
async def trigger_test_notification(
    recipient: Annotated[str | None, Query(description="Optional custom recipient email")] = None,
) -> dict[str, Any]:
    """Trigger a synthetic P1 test notification through Mailpit SMTP."""
    res = await _service.send_p1_alert(
        alert_id="P1-TEST-SYNTHETIC",
        title="Simulated Depot Inflow Disruption & Rapid Depletion Hazard",
        severity="CRITICAL",
        entity_id="dp-1",
        causal_driver="Bulk pipeline delivery delayed by 18 ticks; station demand surging 1.4x above nominal baseline.",
        recommended_action=(
            "Execute emergency regional reallocation plan #4092 "
            "and throttle commercial sales to preserve hospital generator supply."
        ),
        tick=145,
        recipient=recipient,
    )
    return res


@router.post("/dispatch")
async def dispatch_notification(req: NotificationRequest) -> dict[str, Any]:
    """Dispatch a custom operational alert notification."""
    res = await _service.send_p1_alert(
        alert_id=req.alert_id,
        title=req.title,
        severity=req.severity,
        entity_id=req.entity_id,
        causal_driver=req.causal_driver,
        recommended_action=req.recommended_action,
        tick=req.tick,
        recipient=req.recipient,
    )
    return res


@router.get("/history")
async def get_history() -> dict[str, Any]:
    """Fetch recent alert notification dispatch history and statuses."""
    history = get_notification_history()
    return {"total": len(history), "items": history}
