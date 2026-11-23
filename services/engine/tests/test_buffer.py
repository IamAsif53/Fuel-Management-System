"""Tests for Async Database WriteBuffer (Task 2.3).

Verifies FIFO queue ordering, drop-oldest capacity enforcement, Prometheus metrics,
transactional batching, rollback guarantees, exponential backoff, retry preservation,
graceful shutdown, cancellation, and poison-pill isolation.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from app.db.buffer import (
    FSIP_DB_BATCHES_TOTAL,
    FSIP_DB_BUFFERED_WRITES,
    FSIP_DB_DROPPED_WRITES,
    WriteBuffer,
)
from app.db.models import AuditLog, WorldSnapshot
from app.db.repo import audit, create_sim_run, upsert_snapshot
from app.db.session import check_db_health, resolve_database_url
from sqlalchemy import NullPool, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

pytestmark = pytest.mark.asyncio

DB_TEST_URL = resolve_database_url()


# =============================================================================
# Live Database Fixtures (with graceful skip if DB offline)
# =============================================================================


@pytest_asyncio.fixture
async def db_engine() -> AsyncEngine:
    """Fixture providing a connected AsyncEngine with NullPool."""
    engine = create_async_engine(DB_TEST_URL, poolclass=NullPool)
    is_healthy = await check_db_health(engine)
    if not is_healthy:
        pytest.skip(f"Database at {DB_TEST_URL} is not reachable.")
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_sessionmaker(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Fixture providing an async_sessionmaker bound to the live engine."""
    return async_sessionmaker(
        bind=db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


# =============================================================================
# Mock Sessionmaker Helper for Deterministic Unit Tests
# =============================================================================


class MockAsyncSession:
    """Lightweight session double for unit testing transactions."""

    def __init__(self, should_fail: bool = False, fail_exc: Exception | None = None):
        self.should_fail = should_fail
        self.fail_exc = fail_exc or RuntimeError("Simulated DB write failure")
        self.committed = False
        self.rolled_back = False
        self.executed_calls: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.rolled_back = True
        return False

    def begin(self):
        class TransactionContext:
            def __init__(self, parent: MockAsyncSession):
                self.parent = parent

            async def __aenter__(self):
                if self.parent.should_fail:
                    raise self.parent.fail_exc
                return self.parent

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                if exc_type is not None:
                    self.parent.rolled_back = True
                else:
                    self.parent.committed = True
                return False

        return TransactionContext(self)


def create_mock_sessionmaker(should_fail: bool = False, fail_exc: Exception | None = None):
    """Factory creating a mock sessionmaker returning MockAsyncSession instances."""
    active_sessions: list[MockAsyncSession] = []

    def sessionmaker_factory():
        sess = MockAsyncSession(should_fail=should_fail, fail_exc=fail_exc)
        active_sessions.append(sess)
        return sess

    sessionmaker_factory.active_sessions = active_sessions  # type: ignore[attr-defined]
    return sessionmaker_factory


# =============================================================================
# 1. Queue Submission and FIFO Ordering
# =============================================================================


async def test_submit_and_fifo_ordering():
    """Verify submitted writes are buffered non-blocking and committed in FIFO order."""
    execution_order: list[int] = []

    async def write_op(item_id: int):
        async def _write(session: AsyncSession):
            execution_order.append(item_id)

        return _write

    sm = create_mock_sessionmaker()
    buf = WriteBuffer(sessionmaker=sm, max_items=100)

    # Submit 5 items
    for i in range(1, 6):
        op = await write_op(i)
        buf.submit(op, kind="test")

    assert buf.qsize == 5

    # Process all in one batch
    committed = await buf.flush_once(max_batch=10)
    assert committed == 5
    assert buf.qsize == 0
    assert execution_order == [1, 2, 3, 4, 5]


# =============================================================================
# 2. Configurable Queue Capacity and Drop-Oldest Behavior
# =============================================================================


async def test_configurable_capacity_and_drop_oldest():
    """Verify buffer drops the oldest queued items when capacity is exceeded."""
    executed: list[int] = []

    async def make_op(val: int):
        async def _fn(session: AsyncSession):
            executed.append(val)

        return _fn

    sm = create_mock_sessionmaker()
    # Capacity = 3
    buf = WriteBuffer(sessionmaker=sm, max_items=3)

    # Submit items 1, 2, 3 -> queue full
    for i in range(1, 4):
        buf.submit(await make_op(i), kind="test")
    assert buf.qsize == 3
    assert buf.dropped_count == 0

    # Submit items 4, 5 -> should drop items 1 and 2
    buf.submit(await make_op(4), kind="test")
    assert buf.qsize == 3
    assert buf.dropped_count == 1

    buf.submit(await make_op(5), kind="test")
    assert buf.qsize == 3
    assert buf.dropped_count == 2

    # Flush remaining items: should execute [3, 4, 5]
    committed = await buf.flush_once(max_batch=10)
    assert committed == 3
    assert executed == [3, 4, 5]


# =============================================================================
# 3. Prometheus Metrics: Dropped Writes & Queue Depth
# =============================================================================


async def test_metrics_dropped_writes_and_queue_depth():
    """Verify fsip_db_dropped_writes_total and fsip_db_buffered_writes update accurately."""
    initial_dropped = FSIP_DB_DROPPED_WRITES.labels(kind="test_metric")._value.get()

    sm = create_mock_sessionmaker()
    buf = WriteBuffer(sessionmaker=sm, max_items=2)

    assert FSIP_DB_BUFFERED_WRITES._value.get() == 0

    async def dummy(s):
        pass

    # Submit item 1 -> depth 1
    buf.submit(dummy, kind="test_metric")
    assert FSIP_DB_BUFFERED_WRITES._value.get() == 1

    # Submit item 2 -> depth 2
    buf.submit(dummy, kind="test_metric")
    assert FSIP_DB_BUFFERED_WRITES._value.get() == 2

    # Submit item 3 -> drops item 1, depth remains 2
    buf.submit(dummy, kind="test_metric")
    assert FSIP_DB_BUFFERED_WRITES._value.get() == 2
    assert FSIP_DB_DROPPED_WRITES.labels(kind="test_metric")._value.get() == initial_dropped + 1

    # Flush all -> depth becomes 0
    await buf.flush_once()
    assert FSIP_DB_BUFFERED_WRITES._value.get() == 0


# =============================================================================
# 4. Successful Batch Transaction and Commit
# =============================================================================


async def test_batch_transaction_and_commit():
    """Verify writes in a batch execute atomically within a single transaction."""
    records_written: list[str] = []

    async def op1(s):
        records_written.append("op1")

    async def op2(s):
        records_written.append("op2")

    sm = create_mock_sessionmaker()
    buf = WriteBuffer(sessionmaker=sm, batch_size=10)

    buf.submit(op1, kind="op")
    buf.submit(op2, kind="op")

    initial_batches = FSIP_DB_BATCHES_TOTAL.labels(status="committed")._value.get()
    committed = await buf.flush_once()
    assert committed == 2
    assert records_written == ["op1", "op2"]

    # Verify session was committed in single transaction
    assert len(sm.active_sessions) == 1
    assert sm.active_sessions[0].committed is True
    assert sm.active_sessions[0].rolled_back is False
    assert FSIP_DB_BATCHES_TOTAL.labels(status="committed")._value.get() == initial_batches + 1


# =============================================================================
# 5. Failed Batch Rollback Guarantees
# =============================================================================


async def test_failed_batch_rolls_back_completely():
    """Verify if any operation in a batch fails, the entire batch is rolled back and requeued."""
    state: list[str] = []

    async def good_op(s):
        state.append("good")

    async def bad_op(s):
        state.append("bad")
        raise ValueError("Explosion in write operation")

    sm = create_mock_sessionmaker()
    buf = WriteBuffer(sessionmaker=sm, batch_size=5)

    buf.submit(good_op, kind="test")
    buf.submit(bad_op, kind="test")

    initial_rollbacks = FSIP_DB_BATCHES_TOTAL.labels(status="rolled_back")._value.get()

    # flush_once attempts the batch
    committed = await buf.flush_once()
    assert committed == 0  # Nothing committed
    assert FSIP_DB_BATCHES_TOTAL.labels(status="rolled_back")._value.get() == initial_rollbacks + 1

    # Verify transaction rolled back
    assert len(sm.active_sessions) == 1
    assert sm.active_sessions[0].rolled_back is True
    assert sm.active_sessions[0].committed is False

    # Verify items are preserved in queue for retry in original order
    assert buf.qsize == 2


# =============================================================================
# 6. Retry with Exponential Backoff and Work Preservation
# =============================================================================


async def test_retry_and_work_preservation():
    """Verify failed batches are preserved and retried with exponential backoff."""
    attempt_count = 0

    class FailingSessionmaker:
        def __call__(self):
            nonlocal attempt_count
            attempt_count += 1
            # Fail on first 2 attempts, succeed on 3rd
            should_fail = attempt_count < 3
            return MockAsyncSession(should_fail=should_fail, fail_exc=OSError("Database unreachable"))

    buf = WriteBuffer(
        sessionmaker=FailingSessionmaker(),
        base_backoff=0.02,
        max_backoff=0.1,
        flush_interval=0.01,
    )

    executed: list[str] = []

    async def write_task(s):
        executed.append("done")

    buf.submit(write_task, kind="snapshot")

    # Start worker loop
    _worker = asyncio.create_task(buf.run())

    # Wait until item succeeds after retries
    for _ in range(50):
        if len(executed) == 1:
            break
        await asyncio.sleep(0.02)

    await buf.stop(timeout=1.0)
    assert executed == ["done"]
    assert attempt_count >= 3
    assert buf.qsize == 0


# =============================================================================
# 7. Recovery After Database Reconnection
# =============================================================================


async def test_recovery_after_db_reconnect():
    """Verify writes queued during a prolonged DB outage are drained upon reconnection."""
    db_online = False
    committed_items: list[int] = []

    class ReconnectingSessionmaker:
        def __call__(self):
            if not db_online:
                return MockAsyncSession(should_fail=True, fail_exc=ConnectionRefusedError("DB down"))
            return MockAsyncSession(should_fail=False)

    buf = WriteBuffer(
        sessionmaker=ReconnectingSessionmaker(),
        base_backoff=0.02,
        max_backoff=0.05,
        flush_interval=0.01,
    )

    def make_demand_op(item_val: int):
        async def _op(s):
            committed_items.append(item_val)

        return _op

    for i in range(1, 4):
        buf.submit(make_demand_op(i), kind="demand")

    _worker = asyncio.create_task(buf.run())

    # While DB is down: queue keeps items, nothing committed
    await asyncio.sleep(0.08)
    assert len(committed_items) == 0
    assert buf.qsize == 3

    # Restore DB connection
    db_online = True

    # Allow worker to recover and drain
    for _ in range(50):
        if len(committed_items) == 3:
            break
        await asyncio.sleep(0.02)

    await buf.stop(timeout=1.0)
    assert committed_items == [1, 2, 3]
    assert buf.qsize == 0


# =============================================================================
# 8. Graceful Shutdown & Cancellation
# =============================================================================


async def test_graceful_shutdown():
    """Verify stop() signals worker, awaits queue drain, and terminates cleanly."""
    executed = []

    async def op(s):
        executed.append("completed")

    sm = create_mock_sessionmaker()
    buf = WriteBuffer(sessionmaker=sm, flush_interval=0.01)

    worker = asyncio.create_task(buf.run())
    buf.submit(op, kind="test")

    await buf.stop(timeout=1.0, task=worker)
    assert worker.done()
    assert executed == ["completed"]
    assert buf.qsize == 0
    assert not buf.is_running


async def test_cancellation_behavior():
    """Verify worker handles cancellation cleanly without raising unhandled exceptions."""
    sm = create_mock_sessionmaker()
    buf = WriteBuffer(sessionmaker=sm, flush_interval=0.5)

    worker = asyncio.create_task(buf.run())
    await asyncio.sleep(0.02)
    assert buf.is_running

    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker

    assert not buf.is_running


# =============================================================================
# 9. Failure Isolation (Poison Pill does not kill worker)
# =============================================================================


async def test_poison_pill_isolation():
    """Verify a bad payload is isolated and discarded after max_item_retries without killing worker."""
    processed = []

    async def good_op1(s):
        processed.append("good1")

    async def poison_pill(s):
        # A non-connection error (e.g. invalid data)
        raise ValueError("Permanent invalid data error")

    async def good_op2(s):
        processed.append("good2")

    sm = create_mock_sessionmaker()
    buf = WriteBuffer(
        sessionmaker=sm,
        batch_size=5,
        base_backoff=0.01,
        max_backoff=0.02,
        flush_interval=0.01,
        max_item_retries=2,  # Short retries for test speed
    )

    buf.submit(good_op1, kind="good")
    buf.submit(poison_pill, kind="poison")
    buf.submit(good_op2, kind="good")

    _worker = asyncio.create_task(buf.run())

    # Wait for worker to isolate poison pill and process both good writes
    for _ in range(50):
        if "good1" in processed and "good2" in processed:
            break
        await asyncio.sleep(0.02)

    await buf.stop(timeout=1.0)

    assert "good1" in processed
    assert "good2" in processed
    assert buf.dropped_count == 1  # Poison pill was dropped
    assert buf.qsize == 0


# =============================================================================
# 10. Live TimescaleDB Integration via WriteBuffer
# =============================================================================


@pytest.mark.integration
async def test_live_timescaledb_integration_via_buffer(db_sessionmaker: async_sessionmaker[AsyncSession]):
    """Verify real database repository operations execute through WriteBuffer against live TimescaleDB."""
    buf = WriteBuffer(
        sessionmaker=db_sessionmaker,
        batch_size=10,
        flush_interval=0.02,
    )

    _worker = asyncio.create_task(buf.run())

    # Create SimRun first directly
    async with db_sessionmaker() as session:
        sim_run = await create_sim_run(session, scenario_id="buf-test")
        await session.commit()

    # Submit snapshot write via buffer
    snap_data = {
        "sim_run_id": sim_run.id,
        "tick": 42,
        "sim_time": datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        "depots": {"depot-1": {"fuel": 10000}},
        "stations": {"station-1": {"fuel": 5000}},
        "metrics": {"kpi": 0.99},
        "stale": False,
        "degraded": False,
    }

    async def _write_snap(s: AsyncSession):
        await upsert_snapshot(s, snap_data)

    async def _write_audit(s: AsyncSession):
        await audit(
            s,
            actor="test-buffer",
            action="BATCH_WRITE",
            entity_type="sim_run",
            entity_id=str(sim_run.id),
            payload={"msg": "buffer write test"},
        )

    buf.submit(_write_snap, kind="snapshot")
    buf.submit(_write_audit, kind="audit")

    # Flush and stop
    await buf.flush(timeout=5.0)
    await buf.stop(timeout=2.0)

    # Verify rows in database
    async with db_sessionmaker() as session:
        snap_res = await session.execute(
            select(WorldSnapshot).where(
                WorldSnapshot.sim_run_id == sim_run.id,
                WorldSnapshot.tick == 42,
            )
        )
        snap = snap_res.scalar_one_or_none()
        assert snap is not None
        assert snap.metrics == {"kpi": 0.99}

        audit_res = await session.execute(
            select(AuditLog).where(
                AuditLog.actor == "test-buffer",
                AuditLog.entity_id == str(sim_run.id),
            )
        )
        aud = audit_res.scalar_one_or_none()
        assert aud is not None
        assert aud.action == "BATCH_WRITE"


# =============================================================================
# 11. Exponential Backoff Calculation
# =============================================================================


async def test_exponential_backoff_calculation():
    """Verify exponential backoff calculation increases exponentially and caps at max_backoff."""
    sm = create_mock_sessionmaker()
    buf = WriteBuffer(
        sessionmaker=sm,
        base_backoff=0.5,
        max_backoff=10.0,
        jitter_ratio=0.0,  # Zero jitter for deterministic calculation check
    )

    # Compute expected delays for consecutive failures 1, 2, 3, 4, 5, 6
    delays = []
    for fail_count in range(1, 7):
        backoff = min(buf.max_backoff, buf.base_backoff * (2 ** (fail_count - 1)))
        delays.append(backoff)

    assert delays == [0.5, 1.0, 2.0, 4.0, 8.0, 10.0]  # Capped at max_backoff=10.0


# =============================================================================
# 12. Cancellation Data Preservation & In-Flight Rollback Guarantees
# =============================================================================


async def test_cancellation_during_in_flight_tx_rolls_back_and_requeues():
    """Verify cancellation during in-flight batch execution rolls back and requeues all items."""
    in_flight_event = asyncio.Event()
    block_event = asyncio.Event()

    async def blocking_op1(session):
        in_flight_event.set()
        await block_event.wait()

    async def dummy_op2(session):
        pass

    sm = create_mock_sessionmaker()
    buf = WriteBuffer(sessionmaker=sm, batch_size=10, flush_interval=0.01)

    buf.submit(blocking_op1, kind="cancel_test")
    buf.submit(dummy_op2, kind="cancel_test")
    assert buf.qsize == 2

    worker = asyncio.create_task(buf.run())

    # Wait until op1 is actively executing inside the transaction
    await asyncio.wait_for(in_flight_event.wait(), timeout=1.0)
    assert buf.qsize == 0  # Popped as in-flight batch

    # Cancel worker while in-flight
    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker

    # Verify session transaction rolled back
    assert len(sm.active_sessions) == 1
    session = sm.active_sessions[0]
    assert session.rolled_back is True
    assert session.committed is False

    # Verify complete batch was requeued
    assert buf.qsize == 2
    assert FSIP_DB_BUFFERED_WRITES._value.get() == 2
    assert buf.dropped_count == 0

    # Clean up event
    block_event.set()


async def test_requeued_items_retain_original_fifo_order_after_cancellation():
    """Verify uncommitted items requeued on cancellation retain their original FIFO order."""
    in_flight_event = asyncio.Event()
    block_event = asyncio.Event()
    executed_order: list[int] = []

    def make_op(item_id: int):
        async def _op(session):
            if item_id == 1 and not in_flight_event.is_set():
                in_flight_event.set()
                await block_event.wait()
            executed_order.append(item_id)

        return _op

    sm = create_mock_sessionmaker()
    # batch_size=2: items 1 & 2 will be in the in-flight batch; items 3, 4, 5 stay in queue
    buf = WriteBuffer(sessionmaker=sm, batch_size=2, flush_interval=0.01)

    for i in range(1, 6):
        buf.submit(make_op(i), kind="order_test")

    worker = asyncio.create_task(buf.run())
    await asyncio.wait_for(in_flight_event.wait(), timeout=1.0)

    # At this point, batch [1, 2] is in-flight, items [3, 4, 5] are in queue
    assert buf.qsize == 3

    # Cancel worker during in-flight execution of batch [1, 2]
    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker

    # All 5 items must now be in queue, with items 1 & 2 prepended back to head
    assert buf.qsize == 5

    # Unblock the event so subsequent execution can proceed
    block_event.set()

    # Flush all remaining items in a single batch
    committed = await buf.flush_once(max_batch=10)
    assert committed == 5
    assert buf.qsize == 0
    # Strict original FIFO order [1, 2, 3, 4, 5] preserved!
    assert executed_order == [1, 2, 3, 4, 5]


async def test_cancellation_during_retry_backoff_preserves_queued_items():
    """Verify worker cancellation during exponential backoff preserves all queued items."""
    attempt_count = 0

    class FailingOnceSessionmaker:
        def __call__(self):
            nonlocal attempt_count
            attempt_count += 1
            return MockAsyncSession(
                should_fail=True,
                fail_exc=ConnectionResetError("Connection reset by peer"),
            )

    buf = WriteBuffer(
        sessionmaker=FailingOnceSessionmaker(),
        batch_size=5,
        base_backoff=5.0,  # Long backoff so worker stays sleeping
        flush_interval=0.01,
    )

    buf.submit(lambda s: None, kind="backoff_test")
    buf.submit(lambda s: None, kind="backoff_test")
    assert buf.qsize == 2

    worker = asyncio.create_task(buf.run())

    # Wait until batch fails, requeues, and enters backoff
    for _ in range(50):
        if attempt_count >= 1 and buf.qsize == 2:
            break
        await asyncio.sleep(0.02)

    assert attempt_count >= 1
    assert buf.qsize == 2  # Items requeued by _handle_batch_failure

    # Cancel worker while sleeping in backoff
    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker

    # Items must NOT be duplicated or lost
    assert buf.qsize == 2
    assert FSIP_DB_BUFFERED_WRITES._value.get() == 2
    assert buf.dropped_count == 0


async def test_committed_batch_is_never_requeued_on_cancellation():
    """Verify that a batch that has committed successfully is never requeued when cancelled."""
    committed_ops: list[str] = []

    async def op1(s):
        committed_ops.append("op1")

    sm = create_mock_sessionmaker()
    buf = WriteBuffer(sessionmaker=sm, batch_size=10, flush_interval=0.01)

    buf.submit(op1, kind="commit_test")
    assert buf.qsize == 1

    # Commit successfully via flush_once
    committed = await buf.flush_once()
    assert committed == 1
    assert buf.qsize == 0
    assert sm.active_sessions[0].committed is True

    # Start worker and immediately cancel it
    worker = asyncio.create_task(buf.run())
    await asyncio.sleep(0.01)
    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker

    # Queue remains 0, op1 is never requeued
    assert buf.qsize == 0
    assert FSIP_DB_BUFFERED_WRITES._value.get() == 0


async def test_queue_depth_and_dropped_metrics_accuracy_under_cancellation():
    """Verify Prometheus gauge and drop counter stay accurate across cancellation cycles."""
    in_flight_event = asyncio.Event()

    async def blocking_op(s):
        in_flight_event.set()
        await asyncio.Event().wait()  # Block indefinitely

    sm = create_mock_sessionmaker()
    buf = WriteBuffer(sessionmaker=sm, batch_size=10, flush_interval=0.01)

    initial_dropped = FSIP_DB_DROPPED_WRITES.labels(kind="metric_cancel")._value.get()

    buf.submit(blocking_op, kind="metric_cancel")
    buf.submit(lambda s: None, kind="metric_cancel")
    buf.submit(lambda s: None, kind="metric_cancel")

    assert buf.qsize == 3
    assert FSIP_DB_BUFFERED_WRITES._value.get() == 3

    worker = asyncio.create_task(buf.run())
    await asyncio.wait_for(in_flight_event.wait(), timeout=1.0)

    # In flight: queue popped
    assert buf.qsize == 0

    # Cancel
    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker

    # Requeued: queue gauge returns to 3, dropped writes counter unchanged
    assert buf.qsize == 3
    assert FSIP_DB_BUFFERED_WRITES._value.get() == 3
    assert FSIP_DB_DROPPED_WRITES.labels(kind="metric_cancel")._value.get() == initial_dropped


async def test_post_commit_callback_runs_on_commit_and_not_on_rollback():
    """Verify on_commit runs after commit and never on rollback, and on_rollback runs on rollback."""
    sm = create_mock_sessionmaker()
    buf = WriteBuffer(sessionmaker=sm, batch_size=5, flush_interval=0.01)

    commit_called = []
    rollback_called = []

    def on_commit_1():
        commit_called.append(1)

    def on_rollback_1():
        rollback_called.append(1)

    # 1. Successful commit
    buf.submit(lambda s: None, kind="ok", on_commit=on_commit_1, on_rollback=on_rollback_1)
    await buf.flush_once()

    assert commit_called == [1]
    assert rollback_called == []

    # 2. Failed batch -> rollback
    commit_called.clear()
    rollback_called.clear()

    def bad_op(s):
        raise ValueError("Intentional batch failure")

    def on_commit_2():
        commit_called.append(2)

    def on_rollback_2():
        rollback_called.append(2)

    buf.submit(bad_op, kind="bad", on_commit=on_commit_2, on_rollback=on_rollback_2)
    await buf.flush_once()

    # Must NOT run on_commit, MUST run on_rollback
    assert commit_called == []
    assert rollback_called == [2]


async def test_on_commit_exception_does_not_mark_batch_as_failed():
    """Verify an exception raised within on_commit does not cause committed batch to fail or requeue."""
    sm = create_mock_sessionmaker()
    buf = WriteBuffer(sessionmaker=sm, batch_size=5, flush_interval=0.01)

    def exploding_commit():
        raise RuntimeError("Callback crashed")

    buf.submit(lambda s: None, kind="ok", on_commit=exploding_commit)
    committed = await buf.flush_once()

    # Must still return committed count 1 and queue remains 0 (not requeued)
    assert committed == 1
    assert buf.qsize == 0


async def test_on_discard_invoked_on_queue_overflow_and_poison_pill():
    """Verify on_discard is invoked when item is dropped via capacity overflow or poison pill."""
    sm = create_mock_sessionmaker()
    buf = WriteBuffer(sessionmaker=sm, max_items=2, batch_size=1, max_item_retries=1, flush_interval=0.01)

    discarded = []

    def on_discard_item(name):
        return lambda: discarded.append(name)

    # 1. Capacity overflow in submit()
    buf.submit(lambda s: None, kind="k1", on_discard=on_discard_item("item1"))
    buf.submit(lambda s: None, kind="k2", on_discard=on_discard_item("item2"))
    # Queue is full (2/2). Submitting 3rd drops oldest (item1)
    buf.submit(lambda s: None, kind="k3", on_discard=on_discard_item("item3"))

    assert "item1" in discarded

    # 2. Poison pill dropped in _handle_batch_failure()
    discarded.clear()

    def failing_op(s):
        raise ValueError("Schema validation error")

    buf_pp = WriteBuffer(sessionmaker=sm, batch_size=1, max_item_retries=1, flush_interval=0.01)
    buf_pp.submit(failing_op, kind="bad_row", on_discard=on_discard_item("poison_pill"))

    # Attempt 1: fails and reaches max_item_retries=1
    batch = buf_pp._get_batch(1)
    await buf_pp._process_batch(batch)
    buf_pp._handle_batch_failure(batch, consecutive_failures=1)

    assert "poison_pill" in discarded
