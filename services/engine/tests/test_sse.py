"""Unit tests for the Server-Sent Events (SSE) Listener (Task 1.7)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest
from app.sim.sse import FSIP_SSE_CONNECTED, FSIP_SSE_RECONNECTS, SSEListener


@pytest.fixture
def mock_callbacks():
    on_tick = AsyncMock()
    on_notice = AsyncMock()
    on_connect = AsyncMock()
    return on_tick, on_notice, on_connect


# =============================================================================
# 1. Successful SSE connection
# =============================================================================
@pytest.mark.asyncio
async def test_sse_successful_connection(mock_callbacks):
    on_tick, on_notice, on_connect = mock_callbacks

    async def stream_content():
        yield b": connected\n\n"
        yield b"event: simulation.tick\ndata: {\"tick\": 10}\n\n"
        while True:
            await asyncio.sleep(0.05)
            yield b": keepalive\n\n"

    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, headers={"content-type": "text/event-stream"}, content=stream_content())
    )
    client = httpx.AsyncClient(transport=transport)

    listener = SSEListener(
        base_url="http://test-sim:8000",
        on_tick=on_tick,
        on_notice=on_notice,
        on_connect=on_connect,
        client=client,
    )

    task = asyncio.create_task(listener.run())
    await asyncio.sleep(0.05)
    listener.stop()
    await task
    await client.aclose()

    on_connect.assert_awaited()
    on_tick.assert_awaited_with(10)
    assert listener.reconnects >= 0


# =============================================================================
# 2. Handling of connection event and keepalive comments
# =============================================================================
@pytest.mark.asyncio
async def test_sse_handles_connection_event_and_keepalive(mock_callbacks):
    on_tick, on_notice, on_connect = mock_callbacks

    async def stream_content():
        yield b": connected\n\n"
        yield b": keepalive\n\n"
        yield b": another comment\n\n"
        yield b"event: simulation.tick\ndata: {\"tick\": 1}\n\n"
        while True:
            await asyncio.sleep(0.05)
            yield b": keepalive\n\n"

    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, headers={"content-type": "text/event-stream"}, content=stream_content())
    )
    client = httpx.AsyncClient(transport=transport)

    listener = SSEListener(
        base_url="http://test-sim:8000",
        on_tick=on_tick,
        on_notice=on_notice,
        on_connect=on_connect,
        client=client,
    )

    task = asyncio.create_task(listener.run())
    await asyncio.sleep(0.05)
    listener.stop()
    await task
    await client.aclose()

    # Did not crash, tick was processed
    on_tick.assert_awaited_once_with(1)


# =============================================================================
# 3. Correct handling of simulation.tick
# =============================================================================
@pytest.mark.asyncio
async def test_sse_correct_handling_of_simulation_tick(mock_callbacks):
    on_tick, on_notice, on_connect = mock_callbacks

    async def stream_content():
        yield b"event: simulation.tick\ndata: {\"tick\": 42, \"sim_time\": \"2026-03-01T10:00:00Z\"}\n\n"

    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, headers={"content-type": "text/event-stream"}, content=stream_content())
    )
    client = httpx.AsyncClient(transport=transport)

    listener = SSEListener(
        base_url="http://test-sim:8000",
        on_tick=on_tick,
        on_notice=on_notice,
        on_connect=on_connect,
        client=client,
    )

    task = asyncio.create_task(listener.run())
    await asyncio.sleep(0.05)
    listener.stop()
    await task
    await client.aclose()

    on_tick.assert_awaited_once_with(42)


# =============================================================================
# 4. Non-tick events do not incorrectly trigger tick synchronization
# =============================================================================
@pytest.mark.asyncio
async def test_sse_non_tick_events_do_not_trigger_tick(mock_callbacks):
    on_tick, on_notice, on_connect = mock_callbacks

    async def stream_content():
        yield b"event: allocation.status_changed\ndata: {\"id\": 10, \"status\": \"IN_TRANSIT\"}\n\n"
        yield b"event: inventory.updated\ndata: {\"entity_type\": \"depot\", \"entity_id\": \"depot-gazipur\"}\n\n"
        yield b"event: simulator.notice\ndata: {\"message\": \"Simulation reset\"}\n\n"

    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, headers={"content-type": "text/event-stream"}, content=stream_content())
    )
    client = httpx.AsyncClient(transport=transport)

    listener = SSEListener(
        base_url="http://test-sim:8000",
        on_tick=on_tick,
        on_notice=on_notice,
        on_connect=on_connect,
        client=client,
    )

    task = asyncio.create_task(listener.run())
    await asyncio.sleep(0.05)
    listener.stop()
    await task
    await client.aclose()

    # on_tick must NOT be called for non-tick events
    on_tick.assert_not_awaited()
    # on_notice should be called for simulator.notice
    on_notice.assert_awaited_once_with({"message": "Simulation reset"})


# =============================================================================
# 5. Full sync runs after initial connection
# =============================================================================
@pytest.mark.asyncio
async def test_sse_full_sync_on_initial_connect(mock_callbacks):
    on_tick, on_notice, on_connect = mock_callbacks

    async def stream_content():
        yield b": connected\n\n"
        while True:
            await asyncio.sleep(0.05)
            yield b": keepalive\n\n"

    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, headers={"content-type": "text/event-stream"}, content=stream_content())
    )
    client = httpx.AsyncClient(transport=transport)

    listener = SSEListener(
        base_url="http://test-sim:8000",
        on_tick=on_tick,
        on_notice=on_notice,
        on_connect=on_connect,
        client=client,
    )

    task = asyncio.create_task(listener.run())
    await asyncio.sleep(0.05)
    listener.stop()
    await task
    await client.aclose()

    on_connect.assert_awaited_once()


# =============================================================================
# 6. Full sync runs after reconnection
# =============================================================================
@pytest.mark.asyncio
async def test_sse_full_sync_on_reconnect(mock_callbacks):
    on_tick, on_notice, on_connect = mock_callbacks

    session_count = 0

    async def stream_content():
        nonlocal session_count
        session_count += 1
        yield b": connected\n\n"
        if session_count == 1:
            return  # End session abruptly to force reconnect

    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, headers={"content-type": "text/event-stream"}, content=stream_content())
    )
    client = httpx.AsyncClient(transport=transport)

    listener = SSEListener(
        base_url="http://test-sim:8000",
        on_tick=on_tick,
        on_notice=on_notice,
        on_connect=on_connect,
        client=client,
        initial_backoff=0.01,
        max_backoff=0.05,
    )

    task = asyncio.create_task(listener.run())
    await asyncio.sleep(0.12)
    listener.stop()
    await task
    await client.aclose()

    # Reconnect occurred and on_connect was invoked multiple times
    assert session_count >= 2
    assert on_connect.await_count >= 2


# =============================================================================
# 7. Reconnect backoff increases and respects 30-second maximum
# =============================================================================
@pytest.mark.asyncio
async def test_sse_reconnect_backoff_growth(mock_callbacks):
    on_tick, on_notice, on_connect = mock_callbacks

    listener = SSEListener(
        base_url="http://test-sim:8000",
        on_tick=on_tick,
        initial_backoff=1.0,
        max_backoff=30.0,
    )

    # Initial backoff is 1.0
    assert listener._current_backoff == 1.0

    # Simulate backoff doubling in failure loop
    backoffs = []
    for _ in range(6):
        backoffs.append(listener._current_backoff)
        listener._current_backoff = min(listener.max_backoff, listener._current_backoff * 2.0)

    # 1.0, 2.0, 4.0, 8.0, 16.0, 30.0 (capped)
    assert backoffs == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0]
    assert listener._current_backoff == 30.0


# =============================================================================
# 8. Inactivity after timeout causes reconnection
# =============================================================================
@pytest.mark.asyncio
async def test_sse_inactivity_watchdog_causes_reconnect(mock_callbacks):
    on_tick, on_notice, on_connect = mock_callbacks

    session_count = 0

    async def silent_stream():
        nonlocal session_count
        session_count += 1
        yield b": connected\n\n"
        # Total silence for 0.08s (exceeds inactivity_timeout=0.03s)
        await asyncio.sleep(0.08)

    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, headers={"content-type": "text/event-stream"}, content=silent_stream())
    )
    client = httpx.AsyncClient(transport=transport)

    listener = SSEListener(
        base_url="http://test-sim:8000",
        on_tick=on_tick,
        on_notice=on_notice,
        on_connect=on_connect,
        client=client,
        inactivity_timeout=0.03,
        initial_backoff=0.01,
    )

    task = asyncio.create_task(listener.run())
    await asyncio.sleep(0.12)
    listener.stop()
    await task
    await client.aclose()

    # Watchdog fired and triggered reconnect
    assert session_count >= 2
    assert listener.reconnects >= 1


# =============================================================================
# 9. Keepalive comments prevent false inactivity detection
# =============================================================================
@pytest.mark.asyncio
async def test_sse_keepalive_prevents_inactivity_reconnect(mock_callbacks):
    on_tick, on_notice, on_connect = mock_callbacks

    session_count = 0

    async def keepalive_stream():
        nonlocal session_count
        session_count += 1
        yield b": connected\n\n"
        # Yield keepalive every 0.02s (within inactivity_timeout=0.04s)
        for _ in range(3):
            await asyncio.sleep(0.02)
            yield b": keepalive\n\n"
        await asyncio.sleep(0.02)

    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, headers={"content-type": "text/event-stream"}, content=keepalive_stream())
    )
    client = httpx.AsyncClient(transport=transport)

    listener = SSEListener(
        base_url="http://test-sim:8000",
        on_tick=on_tick,
        on_notice=on_notice,
        on_connect=on_connect,
        client=client,
        inactivity_timeout=0.04,
        initial_backoff=0.01,
    )

    task = asyncio.create_task(listener.run())
    await asyncio.sleep(0.07)
    listener.stop()
    await task
    await client.aclose()

    # Single session survived due to keepalives resetting watchdog
    assert session_count == 1
    assert listener.reconnects == 0


# =============================================================================
# 10. Stream HTTP/network failures do not crash the listener
# =============================================================================
@pytest.mark.asyncio
async def test_sse_network_failures_do_not_crash_listener(mock_callbacks):
    on_tick, on_notice, on_connect = mock_callbacks

    attempt = 0

    def faulty_handler(req):
        nonlocal attempt
        attempt += 1
        if attempt <= 2:
            return httpx.Response(503, text="Service Unavailable")

        async def ok_stream():
            yield b": connected\n\n"
            yield b"event: simulation.tick\ndata: {\"tick\": 5}\n\n"

        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=ok_stream())

    transport = httpx.MockTransport(faulty_handler)
    client = httpx.AsyncClient(transport=transport)

    listener = SSEListener(
        base_url="http://test-sim:8000",
        on_tick=on_tick,
        client=client,
        initial_backoff=0.01,
    )

    task = asyncio.create_task(listener.run())
    await asyncio.sleep(0.12)
    listener.stop()
    await task
    await client.aclose()

    # Survived 503 errors and successfully received tick 5
    assert attempt >= 3
    on_tick.assert_awaited_with(5)


# =============================================================================
# 11. Cancellation and shutdown are clean
# =============================================================================
@pytest.mark.asyncio
async def test_sse_clean_cancellation_and_stop(mock_callbacks):
    on_tick, on_notice, on_connect = mock_callbacks

    async def infinite_stream():
        yield b": connected\n\n"
        while True:
            await asyncio.sleep(0.05)
            yield b": keepalive\n\n"

    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, headers={"content-type": "text/event-stream"}, content=infinite_stream())
    )
    client = httpx.AsyncClient(transport=transport)

    listener = SSEListener(
        base_url="http://test-sim:8000",
        on_tick=on_tick,
        client=client,
    )

    task = asyncio.create_task(listener.run())
    await asyncio.sleep(0.02)
    assert listener.connected is True

    # Stop gracefully
    listener.stop()
    await asyncio.wait_for(task, timeout=0.5)

    assert task.done()
    assert listener.connected is False
    await client.aclose()


# =============================================================================
# 12. SSE metrics update correctly
# =============================================================================
@pytest.mark.asyncio
async def test_sse_metrics_update_correctly(mock_callbacks):
    on_tick, on_notice, on_connect = mock_callbacks

    assert FSIP_SSE_CONNECTED is not None
    assert FSIP_SSE_RECONNECTS is not None

    async def stream_content():
        yield b": connected\n\n"
        while True:
            await asyncio.sleep(0.05)
            yield b": keepalive\n\n"

    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, headers={"content-type": "text/event-stream"}, content=stream_content())
    )
    client = httpx.AsyncClient(transport=transport)

    listener = SSEListener(
        base_url="http://test-sim:8000",
        on_tick=on_tick,
        client=client,
        initial_backoff=0.01,
    )

    task = asyncio.create_task(listener.run())
    await asyncio.sleep(0.03)

    # Connected metric reflects 1
    assert FSIP_SSE_CONNECTED._value.get() == 1.0

    listener.stop()
    await task
    await client.aclose()

    # Metric reset to 0 after stop
    assert FSIP_SSE_CONNECTED._value.get() == 0.0
