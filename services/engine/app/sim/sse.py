"""Server-Sent Events (SSE) listener for BUP Fuel Supply Resilience Platform.

Features:
- Connects to simulator /v1/stream endpoint using httpx-sse.
- Handles connection event (: connected) and keepalive comments (: keepalive).
- Inactivity watchdog: closes connection and triggers reconnect if no bytes arrive for 30 seconds.
- Keepalive comments count as received bytes, resetting the inactivity watchdog.
- simulation.tick passes tick to SyncLoop.request_cycle.
- simulator.notice passes payload to on_notice callback (e.g. for reset detection).
- Triggers on_connect callback (SyncLoop.full_sync) immediately upon initial connect and every reconnect.
- Reconnect backoff starts at 1s, doubling up to 30s (+jitter), resetting to 1s on stable connection.
- Prometheus metrics: fsip_sse_connected (Gauge) and fsip_sse_reconnects_total (Counter).
- Clean cancellation and shutdown handling.
"""
from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from fsip_common.logging import get_logger
from httpx_sse import ServerSentEvent, aconnect_sse
from httpx_sse._decoders import SSEDecoder
from prometheus_client import REGISTRY, Counter, Gauge

log = get_logger()

# ---------------------------------------------------------------- Prometheus Metrics
try:
    FSIP_SSE_CONNECTED = Gauge(
        "fsip_sse_connected",
        "Current SSE connection state: 1=connected, 0=disconnected",
    )
except ValueError:
    FSIP_SSE_CONNECTED = REGISTRY._names_to_collectors.get("fsip_sse_connected")  # type: ignore[assignment]

try:
    FSIP_SSE_RECONNECTS = Counter(
        "fsip_sse_reconnects_total",
        "Total number of SSE reconnect attempts",
    )
except ValueError:
    FSIP_SSE_RECONNECTS = REGISTRY._names_to_collectors.get("fsip_sse_reconnects_total")  # type: ignore[assignment]


# ---------------------------------------------------------------- SSEListener
class SSEListener:
    """Streams real-time events from the simulator via Server-Sent Events."""

    def __init__(
        self,
        base_url: str,
        on_tick: Callable[[int], Awaitable[None]],
        on_notice: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        on_connect: Callable[[], Awaitable[None]] | None = None,
        client: httpx.AsyncClient | None = None,
        inactivity_timeout: float = 30.0,
        initial_backoff: float = 1.0,
        max_backoff: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.stream_url = f"{self.base_url}/v1/stream"
        self.on_tick = on_tick
        self.on_notice = on_notice
        self.on_connect = on_connect
        self.inactivity_timeout = inactivity_timeout
        self.initial_backoff = initial_backoff
        self.max_backoff = max_backoff

        self._external_client = client
        self.connected: bool = False
        self.reconnects: int = 0
        self._current_backoff = self.initial_backoff
        self._stop_event = asyncio.Event()

    def stop(self) -> None:
        """Signal the listener to terminate gracefully."""
        self._stop_event.set()

    def _update_connected_metric(self, is_connected: bool) -> None:
        self.connected = is_connected
        if FSIP_SSE_CONNECTED is not None:
            FSIP_SSE_CONNECTED.set(1 if is_connected else 0)

    async def _dispatch_event(self, sse: ServerSentEvent) -> None:
        """Route named SSE events to appropriate domain handlers."""
        event_name = sse.event or "message"

        if event_name == "simulation.tick":
            try:
                payload = json.loads(sse.data) if sse.data else {}
                tick = int(payload["tick"])
                log.debug("sse.tick_received", tick=tick)
                await self.on_tick(tick)
            except Exception as exc:
                log.warning("sse.invalid_tick_payload", error=str(exc), raw_data=sse.data)

        elif event_name == "simulator.notice":
            try:
                payload = json.loads(sse.data) if sse.data else {}
                log.info("sse.simulator_notice", payload=payload)
                if self.on_notice:
                    await self.on_notice(payload)
            except Exception as exc:
                log.warning("sse.invalid_notice_payload", error=str(exc), raw_data=sse.data)

        elif event_name in ("allocation.status_changed", "inventory.updated"):
            # Informational event - state truth is refreshed from REST on tick
            log.debug("sse.event_ignored_for_sync", event_type=event_name)

        else:
            log.debug("sse.unknown_event", event_type=event_name)

    async def run(self) -> None:
        """Continuously maintain SSE connection with exponential backoff and watchdog."""
        log.info("sse.listener_started", url=self.stream_url)

        own_client = None
        if self._external_client is None:
            own_client = httpx.AsyncClient(timeout=httpx.Timeout(connect=5.0, read=None, write=5.0, pool=5.0))
        http_client = self._external_client or own_client

        try:
            while not self._stop_event.is_set():
                if self.reconnects > 0:
                    jitter = random.uniform(0.0, 0.25 * self._current_backoff)
                    delay = min(self.max_backoff, self._current_backoff + jitter)
                    log.info("sse.reconnecting", delay_s=round(delay, 2), reconnect_count=self.reconnects)
                    try:
                        await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
                        break  # Stop requested during backoff
                    except TimeoutError:
                        pass
                    self._current_backoff = min(self.max_backoff, self._current_backoff * 2.0)

                if self._stop_event.is_set():
                    break

                try:
                    await self._stream_session(http_client)
                except asyncio.CancelledError:
                    log.info("sse.cancelled")
                    break
                except Exception as exc:
                    log.warning("sse.session_error", error=str(exc))
                finally:
                    self._update_connected_metric(False)

                if not self._stop_event.is_set():
                    self.reconnects += 1
                    if FSIP_SSE_RECONNECTS is not None:
                        FSIP_SSE_RECONNECTS.inc()
                    log.info("sse.disconnected", reconnect_count=self.reconnects)

        finally:
            self._update_connected_metric(False)
            if own_client is not None:
                await own_client.aclose()
            log.info("sse.listener_stopped")

    async def _stream_session(self, client: httpx.AsyncClient) -> None:
        """Execute a single active streaming session with inactivity watchdog."""
        async with aconnect_sse(client, "GET", self.stream_url) as es:
            if es.response.status_code != 200:
                raise httpx.HTTPStatusError(
                    f"SSE stream returned HTTP {es.response.status_code}",
                    request=es.response.request,
                    response=es.response,
                )

            self._update_connected_metric(True)
            self._current_backoff = self.initial_backoff
            log.info("sse.connected", url=self.stream_url)

            # Trigger resync immediately after successful connection/reconnection
            if self.on_connect:
                try:
                    await self.on_connect()
                except Exception as exc:
                    log.error("sse.on_connect_failed", error=str(exc))

            decoder = SSEDecoder()
            line_iter = es.response.aiter_lines()

            while not self._stop_event.is_set():
                try:
                    line = await asyncio.wait_for(line_iter.__anext__(), timeout=self.inactivity_timeout)
                except StopAsyncIteration:
                    log.info("sse.stream_eof")
                    break
                except TimeoutError:
                    log.warning("sse.inactivity_watchdog_fired", timeout_s=self.inactivity_timeout)
                    break

                # Line arrived: watchdog is reset
                line_clean = line.rstrip("\r\n")

                if line_clean.startswith(":"):
                    if "connected" in line_clean:
                        log.info("sse.greeting_received")
                    elif "keepalive" in line_clean:
                        log.debug("sse.keepalive_received")
                    continue

                sse = decoder.decode(line_clean)
                if sse is not None:
                    await self._dispatch_event(sse)
