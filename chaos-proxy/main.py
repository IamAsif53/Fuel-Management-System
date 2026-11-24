"""Chaos Proxy for FSIP Simulator (Phase 10: Chaos Drills).

Acts as a transparent reverse proxy between Engine and Simulator on port 8001.
Allows controlled injection of response corruptions (F6: Malformed Data):
- drop_field: drops required fields
- wrong_type: converts numeric to string or object to list
- nan: replaces number with NaN / null
- negative: injects negative inventory or capacity
- unknown_enum: injects invalid status enums
"""

from __future__ import annotations

import os
import random
from typing import Any

import httpx
from fastapi import FastAPI, Request, Response
from pydantic import BaseModel, Field

SIMULATOR_URL = os.getenv("SIMULATOR_URL", "http://simulator:8000").rstrip("/")

app = FastAPI(title="FSIP Chaos Proxy", version="1.0.0")

# Active corruption rules: resource -> {mode, rate}
active_rules: dict[str, dict[str, Any]] = {}


class MalformRuleRequest(BaseModel):
    resource: str = Field(..., description="stations, depots, routes, events, metrics, or demand")
    mode: str = Field(..., description="drop_field, wrong_type, nan, negative, unknown_enum")
    rate: float = Field(1.0, ge=0.0, le=1.0, description="Corruption probability between 0.0 and 1.0")


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "healthy", "service": "chaos-proxy", "active_rules": active_rules}


@app.post("/chaos/malform")
async def add_malform_rule(req: MalformRuleRequest) -> dict[str, Any]:
    active_rules[req.resource.lower()] = {"mode": req.mode.lower(), "rate": req.rate}
    return {"status": "ok", "message": f"Rule set for {req.resource}", "rules": active_rules}


@app.post("/chaos/clear")
async def clear_rules() -> dict[str, Any]:
    active_rules.clear()
    return {"status": "ok", "message": "all corruption rules cleared", "rules": {}}


@app.get("/chaos/status")
async def get_rules() -> dict[str, Any]:
    return {"status": "ok", "rules": active_rules}


def _corrupt_payload(resource: str, data: Any, mode: str) -> Any:
    """Corrupt payload according to the specified mode."""
    if not isinstance(data, dict | list):
        return data

    target = data[0] if isinstance(data, list) and len(data) > 0 else data

    if isinstance(target, dict):
        if mode == "drop_field":
            # Drop a critical field
            for k in ("capacity", "inventory", "status", "demand_profile", "transit_ticks", "tick"):
                if k in target:
                    target.pop(k, None)
                    break
            else:
                if target:
                    first_k = next(iter(target.keys()))
                    target.pop(first_k, None)

        elif mode == "wrong_type":
            for k in ("capacity", "inventory", "transit_ticks", "tick", "quantity"):
                if k in target:
                    target[k] = "INVALID_STRING_VALUE"
                    break

        elif mode == "nan":
            for k in ("capacity", "inventory", "transit_ticks", "tick", "quantity"):
                if k in target:
                    target[k] = None
                    break

        elif mode == "negative":
            for k in ("capacity", "inventory", "transit_ticks", "quantity"):
                if k in target:
                    if isinstance(target[k], dict):
                        for sub_k in target[k]:
                            target[k][sub_k] = -9999.0
                    else:
                        target[k] = -999.0
                    break

        elif mode == "unknown_enum":
            for k in ("status", "demand_profile", "event_type"):
                if k in target:
                    target[k] = "UNKNOWN_NONEXISTENT_ENUM_VALUE"
                    break

    return data


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
async def proxy_request(request: Request, path: str) -> Response:
    """Transparently proxy request to Simulator, applying active corruption rules to matching responses."""
    url = f"{SIMULATOR_URL}/{path}"
    query = str(request.url.query)
    if query:
        url = f"{url}?{query}"

    method = request.method
    headers = dict(request.headers)
    headers.pop("host", None)
    headers.pop("content-length", None)

    body = await request.body()

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.request(
                method=method,
                url=url,
                headers=headers,
                content=body if body else None,
            )
        except Exception as exc:
            return Response(content=f'{{"error": "{str(exc)}"}}', status_code=502, media_type="application/json")

    resp_headers = dict(resp.headers)
    resp_headers.pop("content-length", None)
    content_type = resp_headers.get("content-type", "")

    # Check corruption rules on JSON responses
    if "application/json" in content_type and resp.status_code == 200:
        for resource, rule in active_rules.items():
            if resource in path.lower():
                rate = rule.get("rate", 1.0)
                if random.random() <= rate:
                    try:
                        data = resp.json()
                        corrupted = _corrupt_payload(resource, data, rule.get("mode", "drop_field"))
                        import json
                        new_body = json.dumps(corrupted).encode("utf-8")
                        return Response(
                            content=new_body,
                            status_code=resp.status_code,
                            headers=resp_headers,
                            media_type="application/json",
                        )
                    except Exception:
                        pass

    return Response(content=resp.content, status_code=resp.status_code, headers=resp_headers)
