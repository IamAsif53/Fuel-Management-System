"""Phase 19: Notification Escalation Service for Critical P1 Alerts.

Connects to Mailpit (or configured SMTP server) to deliver operator escalation emails
for P1 critical incidents, stockout hazards, and resilience mode transitions.
"""
from __future__ import annotations

import asyncio
import os
import smtplib
from datetime import UTC, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from fsip_common.logging import get_logger

log = get_logger()

# In-memory circular notification log (last 100 alerts)
_NOTIFICATION_HISTORY: list[dict[str, Any]] = []


class NotificationService:
    def __init__(self) -> None:
        self.smtp_host = os.environ.get("FSIP_SMTP_HOST", "localhost")
        self.smtp_port = int(os.environ.get("FSIP_SMTP_PORT", "1025"))
        self.sender = os.environ.get("FSIP_SMTP_SENDER", "alerts@fsip.internal")
        self.default_recipient = os.environ.get("FSIP_SMTP_RECIPIENT", "duty-operator@fsip.internal")
        self.enabled = os.environ.get("FSIP_NOTIFICATIONS_ENABLED", "true").lower() in ("true", "1", "yes")

    def _send_sync(self, msg: MIMEMultipart, recipient: str) -> None:
        """Synchronous SMTP transmission run in worker thread."""
        with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=3.0) as server:
            server.send_message(msg)

    async def send_p1_alert(
        self,
        alert_id: str | int,
        title: str,
        severity: str,
        entity_id: str,
        causal_driver: str,
        recommended_action: str,
        tick: int | None = None,
        recipient: str | None = None,
    ) -> dict[str, Any]:
        """Deliver formatted P1 escalation alert to operator via SMTP."""
        rec = recipient or self.default_recipient
        now_iso = datetime.now(UTC).isoformat()
        dispatch_id = f"notif-{int(datetime.now(UTC).timestamp()*1000)}"

        record: dict[str, Any] = {
            "dispatch_id": dispatch_id,
            "alert_id": alert_id,
            "title": title,
            "severity": severity,
            "entity_id": entity_id,
            "recipient": rec,
            "dispatched_at": now_iso,
            "status": "PENDING",
            "error": None,
        }

        if not self.enabled:
            record["status"] = "SKIPPED_DISABLED"
            _NOTIFICATION_HISTORY.append(record)
            return record

        # Format MIME Email
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[FSIP {severity.upper()} ALERT] {title} — Entity: {entity_id}"
        msg["From"] = f"FSIP Autonomous Monitor <{self.sender}>"
        msg["To"] = rec
        msg["X-FSIP-Severity"] = severity
        msg["X-FSIP-Entity"] = entity_id

        # Plaintext Body
        text_content = f"""
============================================================
FSIP CRITICAL OPERATOR ESCALATION — P1 ALERT
============================================================
Severity:           {severity}
Alert ID:           {alert_id}
Entity ID:          {entity_id}
Simulation Tick:    {tick or 'N/A'}
Timestamp:          {now_iso}

Summary:
{title}

Root Cause Analysis:
{causal_driver}

Recommended Autonomous / Operator Action:
{recommended_action}

Action Link: http://localhost:3000/decisions
============================================================
Automated alert delivered by Fuel Supply Intelligence Platform.
"""

        # HTML Body
        html_content = f"""
<!DOCTYPE html>
<html>
<head>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
      background: #0b0f19;
      color: #f1f5f9;
      padding: 20px;
    }}
    .card {{
      background: #1e293b;
      border: 1px solid #dc2626;
      border-radius: 8px;
      padding: 20px;
      max-width: 600px;
      margin: 0 auto;
    }}
    .badge {{
      background: rgba(220, 38, 38, 0.2);
      border: 1px solid #dc2626;
      color: #f87171;
      padding: 4px 8px;
      border-radius: 4px;
      font-weight: bold;
      font-size: 12px;
    }}
    .title {{ font-size: 18px; font-weight: bold; margin-top: 12px; margin-bottom: 8px; color: #ffffff; }}
    .meta {{ font-size: 12px; color: #94a3b8; margin-bottom: 16px; }}
    .section {{ margin-top: 14px; font-size: 13px; line-height: 1.5; }}
    .label {{ font-weight: 600; color: #cbd5e1; text-transform: uppercase; font-size: 11px; letter-spacing: 0.05em; }}
    .btn {{
      display: inline-block;
      background: #2563eb;
      color: #ffffff;
      padding: 8px 16px;
      border-radius: 6px;
      text-decoration: none;
      font-weight: 600;
      font-size: 13px;
      margin-top: 18px;
    }}
  </style>
</head>
<body>
  <div class="card">
    <div><span class="badge">{severity.upper()} ESCALATION</span></div>
    <div class="title">{title}</div>
    <div class="meta">Target: <b>{entity_id}</b> | Tick: <b>{tick or 'N/A'}</b> | Time: {now_iso}</div>

    <div class="section">
      <div class="label">Primary Causal Driver</div>
      <div>{causal_driver}</div>
    </div>

    <div class="section">
      <div class="label">Recommended Action</div>
      <div>{recommended_action}</div>
    </div>

    <a href="http://localhost:3000/decisions" class="btn">Open Decision Console</a>
  </div>
</body>
</html>
"""
        msg.attach(MIMEText(text_content, "plain"))
        msg.attach(MIMEText(html_content, "html"))

        # Non-blocking async execution
        try:
            await asyncio.to_thread(self._send_sync, msg, rec)
            record["status"] = "DELIVERED"
            log.info("notifier.alert_delivered", dispatch_id=dispatch_id, alert_id=alert_id, recipient=rec)
        except Exception as exc:
            record["status"] = "FAILED"
            record["error"] = str(exc)
            log.warning("notifier.dispatch_failed", dispatch_id=dispatch_id, error=str(exc))

        _NOTIFICATION_HISTORY.append(record)
        if len(_NOTIFICATION_HISTORY) > 100:
            _NOTIFICATION_HISTORY.pop(0)

        return record


def get_notification_history() -> list[dict[str, Any]]:
    return list(reversed(_NOTIFICATION_HISTORY))
