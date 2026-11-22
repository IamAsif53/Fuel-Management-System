"""State endpoint router (/api/state)."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.services.state_service import get_current_state

router = APIRouter(prefix="/api", tags=["state"])


@router.get("/state")
async def get_state_endpoint(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> Response:
    """Retrieve full live WorldState projection with 304 ETag caching and DB fallback."""
    redis_client = getattr(request.app.state, "redis", None)
    state, tick, is_degraded, etag = await get_current_state(redis_client, session)

    headers = {
        "X-Data-Tick": str(tick),
        "X-Degraded": "true" if is_degraded else "false",
        "ETag": etag,
    }

    # Evaluate If-None-Match header
    if if_none_match:
        client_tag = if_none_match.strip()
        server_tag = etag.strip()
        raw_server_tag = server_tag.strip('"')
        raw_client_tag = client_tag.strip('"')

        if client_tag == server_tag or raw_client_tag == raw_server_tag:
            return Response(status_code=304, headers=headers)

    return JSONResponse(content=state, headers=headers)
