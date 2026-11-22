"""Asynchronous database write buffer (Task 2.3).

Provides a bounded in-memory buffer and background worker for asynchronous
database writes, preventing engine stall during database latency or transient
outages. Follows 23_MODULE_SPECS.md §B8 and PHASE_02_PERSISTENCE_AND_API.md Task 2.3.
"""
from __future__ import annotations

import asyncio
import inspect
import random
import socket
import threading
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from prometheus_client import REGISTRY, Counter, Gauge
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from structlog import get_logger

log = get_logger()

# =============================================================================
# Prometheus Metrics
# =============================================================================

try:
    FSIP_DB_DROPPED_WRITES = Counter(
        "fsip_db_dropped_writes_total",
        "Total database write operations dropped due to buffer capacity limit",
        ["kind"],
    )
except ValueError:
    FSIP_DB_DROPPED_WRITES = REGISTRY._names_to_collectors["fsip_db_dropped_writes_total"]

try:
    FSIP_DB_BUFFERED_WRITES = Gauge(
        "fsip_db_buffered_writes",
        "Current number of pending database writes in buffer queue",
    )
except ValueError:
    FSIP_DB_BUFFERED_WRITES = REGISTRY._names_to_collectors["fsip_db_buffered_writes"]

try:
    FSIP_DB_RETRIES_TOTAL = Counter(
        "fsip_db_write_retries_total",
        "Total retry attempts for failed database write batches",
        ["kind"],
    )
except ValueError:
    FSIP_DB_RETRIES_TOTAL = REGISTRY._names_to_collectors["fsip_db_write_retries_total"]

try:
    FSIP_DB_BATCHES_TOTAL = Counter(
        "fsip_db_write_batches_total",
        "Total database write batches processed",
        ["status"],
    )
except ValueError:
    FSIP_DB_BATCHES_TOTAL = REGISTRY._names_to_collectors["fsip_db_write_batches_total"]


# =============================================================================
# Helper Utilities
# =============================================================================

def is_connection_error(exc: Exception) -> bool:
    """Determine whether an exception is caused by an unreachable or dropped DB connection."""
    if isinstance(exc, ConnectionError | OSError | TimeoutError | socket.error):
        return True
    err_str = f"{type(exc).__module__}.{type(exc).__name__} {str(exc)}".lower()
    connection_keywords = (
        "connection",
        "cannot connect",
        "connect call failed",
        "refused",
        "closed",
        "terminat",
        "broken pipe",
        "reset by peer",
        "timeout",
        "is not reachable",
        "operationalerror",
        "pool timeout",
    )
    return any(kw in err_str for kw in connection_keywords)


# =============================================================================
# Data Structures
# =============================================================================

@dataclass
class WriteItem:
    """Single database write operation wrapped for buffering."""

    fn: Callable[[AsyncSession], Awaitable[None]]
    kind: str = "unknown"
    created_at: float = field(default_factory=time.monotonic)
    attempts: int = 0


# =============================================================================
# WriteBuffer Implementation
# =============================================================================

class WriteBuffer:
    """Asynchronous, bounded, fault-tolerant write buffer with transactional batching."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        max_items: int = 5000,
        batch_size: int = 100,
        flush_interval: float = 0.5,
        base_backoff: float = 0.5,
        max_backoff: float = 30.0,
        jitter_ratio: float = 0.2,
        max_item_retries: int = 5,
    ) -> None:
        self.sessionmaker = sessionmaker
        self.max_items = max_items
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.base_backoff = base_backoff
        self.max_backoff = max_backoff
        self.jitter_ratio = jitter_ratio
        self.max_item_retries = max_item_retries

        self._queue: deque[WriteItem] = deque()
        self._lock = threading.Lock()
        self._wake_event = asyncio.Event()
        self._stop_event = asyncio.Event()

        self._dropped_count = 0
        self._running = False
        self._stopping = False
        self._in_flight = 0
        self._worker_task: asyncio.Task[None] | None = None

        # Initialize gauge to current queue depth
        FSIP_DB_BUFFERED_WRITES.set(0)

    # -------------------------------------------------------------------------
    # Properties
    # -------------------------------------------------------------------------

    @property
    def qsize(self) -> int:
        """Return the current number of buffered write operations."""
        with self._lock:
            return len(self._queue)

    @property
    def dropped_count(self) -> int:
        """Return total count of dropped items since initialization."""
        with self._lock:
            return self._dropped_count

    @property
    def is_running(self) -> bool:
        """Return True if background worker loop is active."""
        return self._running

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def submit(
        self,
        fn: Callable[[AsyncSession], Awaitable[None]],
        kind: str = "unknown",
    ) -> None:
        """Submit an async write operation to the buffer.

        Never blocks. If the buffer is full, discards the oldest queued item,
        accepts the new item, and increments fsip_db_dropped_writes_total.
        """
        with self._lock:
            if len(self._queue) >= self.max_items:
                dropped = self._queue.popleft()
                self._dropped_count += 1
                FSIP_DB_DROPPED_WRITES.labels(kind=dropped.kind).inc()
                log.warning(
                    "db.write_buffer.dropped",
                    kind=dropped.kind,
                    queue_size=len(self._queue),
                    max_items=self.max_items,
                )

            item = WriteItem(fn=fn, kind=kind)
            self._queue.append(item)
            depth = len(self._queue)
            FSIP_DB_BUFFERED_WRITES.set(depth)

        # Wake worker loop outside lock
        self._wake_event.set()

    async def flush_once(self, max_batch: int | None = None) -> int:
        """Process and commit a single batch synchronously.

        Returns number of items successfully committed, or 0 if queue was empty
        or batch execution failed.
        """
        batch = self._get_batch(max_batch)
        if not batch:
            return 0

        self._in_flight += len(batch)
        try:
            success = await self._process_batch(batch)
            if success:
                return len(batch)
            else:
                self._requeue(batch)
                return 0
        finally:
            self._in_flight -= len(batch)

    async def flush(self, timeout: float = 10.0) -> None:
        """Wait for all pending writes in the queue to be committed."""
        start_time = time.monotonic()
        self._wake_event.set()

        while self.qsize > 0 or self._in_flight > 0:
            if time.monotonic() - start_time > timeout:
                raise TimeoutError(
                    f"WriteBuffer.flush() timed out after {timeout}s with {self.qsize} items remaining"
                )
            if not self._running:
                committed = await self.flush_once()
                if committed == 0 and self.qsize > 0:
                    await asyncio.sleep(0.05)
            else:
                await asyncio.sleep(0.02)

    async def stop(self, timeout: float = 5.0, task: asyncio.Task[None] | None = None) -> None:
        """Gracefully stop the worker loop and await queue drain."""
        self._stopping = True
        self._stop_event.set()
        self._wake_event.set()

        target_task = task or self._worker_task
        if target_task and not target_task.done():
            try:
                await asyncio.wait_for(asyncio.shield(target_task), timeout=timeout)
            except (TimeoutError, asyncio.CancelledError):
                target_task.cancel()
                try:
                    await target_task
                except asyncio.CancelledError:
                    pass

    # -------------------------------------------------------------------------
    # Worker Loop
    # -------------------------------------------------------------------------

    async def run(self) -> None:
        """Long-running worker loop that batches pending writes and commits transactions."""
        self._running = True
        self._worker_task = asyncio.current_task()
        consecutive_failures = 0

        log.info(
            "db.write_buffer.worker_started",
            max_items=self.max_items,
            batch_size=self.batch_size,
        )

        try:
            while not self._stopping or self.qsize > 0:
                # 1. Wait until items are available or wake event is set
                while self.qsize == 0:
                    if self._stopping:
                        break
                    self._wake_event.clear()
                    try:
                        await asyncio.wait_for(
                            self._wake_event.wait(),
                            timeout=self.flush_interval,
                        )
                    except TimeoutError:
                        pass

                if self._stopping and self.qsize == 0:
                    break

                # 2. Extract batch (scale down to 1 if retrying to isolate failures)
                effective_batch_size = (
                    1 if consecutive_failures >= 1 else self.batch_size
                )
                batch = self._get_batch(effective_batch_size)
                if not batch:
                    continue

                self._in_flight += len(batch)
                try:
                    # 3. Execute batch inside a single atomic transaction
                    success = await self._process_batch(batch)
                    if success:
                        consecutive_failures = 0
                    else:
                        consecutive_failures += 1
                        await self._handle_batch_failure(batch, consecutive_failures)
                finally:
                    self._in_flight -= len(batch)

        except asyncio.CancelledError:
            log.info("db.write_buffer.cancelled")
            raise
        except Exception as exc:
            log.error("db.write_buffer.fatal_error", error=str(exc), exc_info=True)
            raise
        finally:
            self._running = False
            log.info("db.write_buffer.worker_stopped", remaining_items=self.qsize)

    # -------------------------------------------------------------------------
    # Internal Batch Processing & Resilience
    # -------------------------------------------------------------------------

    def _get_batch(self, max_batch: int | None = None) -> list[WriteItem]:
        """Pop up to max_batch items from queue."""
        limit = max_batch or self.batch_size
        batch: list[WriteItem] = []
        with self._lock:
            to_take = min(len(self._queue), limit)
            for _ in range(to_take):
                batch.append(self._queue.popleft())
            FSIP_DB_BUFFERED_WRITES.set(len(self._queue))
        return batch

    def _requeue(self, batch: list[WriteItem]) -> None:
        """Prepend failed batch back to head of queue preserving FIFO order."""
        with self._lock:
            for item in reversed(batch):
                if len(self._queue) >= self.max_items:
                    self._dropped_count += 1
                    FSIP_DB_DROPPED_WRITES.labels(kind=item.kind).inc()
                    log.warning(
                        "db.write_buffer.requeue_dropped",
                        kind=item.kind,
                        max_items=self.max_items,
                    )
                else:
                    self._queue.appendleft(item)
            FSIP_DB_BUFFERED_WRITES.set(len(self._queue))

    async def _process_batch(self, batch: list[WriteItem]) -> bool:
        """Execute all writes in the batch within a single atomic transaction.

        If any write fails, rolls back the entire batch.
        """
        if not batch:
            return True

        try:
            async with self.sessionmaker() as session:
                async with session.begin():
                    for item in batch:
                        item.attempts += 1
                        res = item.fn(session)
                        if inspect.isawaitable(res):
                            await res

            FSIP_DB_BATCHES_TOTAL.labels(status="committed").inc()
            log.debug("db.write_buffer.batch_committed", count=len(batch))
            return True
        except Exception as exc:
            FSIP_DB_BATCHES_TOTAL.labels(status="rolled_back").inc()
            first_kind = batch[0].kind if batch else "unknown"
            log.warning(
                "db.write_buffer.batch_failed",
                count=len(batch),
                kind=first_kind,
                error=str(exc),
            )
            # Store last error on items for inspection
            for it in batch:
                it.last_error = exc  # type: ignore[attr-defined]
            return False

    async def _handle_batch_failure(
        self,
        batch: list[WriteItem],
        consecutive_failures: int,
    ) -> None:
        """Handle failed batch: isolate poison pills, requeue valid items, and back off."""
        first_kind = batch[0].kind if batch else "unknown"
        FSIP_DB_RETRIES_TOTAL.labels(kind=first_kind).inc()

        # Check for poison pills: if a single item fails repeatedly and error is NOT
        # a connection error, discard that specific item to prevent killing worker.
        items_to_requeue = []
        for item in batch:
            last_err = getattr(item, "last_error", None)
            is_conn = is_connection_error(last_err) if last_err else True

            # If tested individually, not a connection error, and attempts exceeded max_item_retries: drop poison pill
            if len(batch) == 1 and not is_conn and item.attempts >= self.max_item_retries:
                self._dropped_count += 1
                FSIP_DB_DROPPED_WRITES.labels(kind=item.kind).inc()
                log.error(
                    "db.write_buffer.poison_pill_dropped",
                    kind=item.kind,
                    attempts=item.attempts,
                    error=str(last_err),
                )
            else:
                items_to_requeue.append(item)

        if items_to_requeue:
            self._requeue(items_to_requeue)

        # Compute bounded exponential backoff with jitter
        backoff = min(
            self.max_backoff,
            self.base_backoff * (2 ** (consecutive_failures - 1)),
        )
        jitter = backoff * random.uniform(-self.jitter_ratio, self.jitter_ratio)
        sleep_s = max(0.01, backoff + jitter)

        log.info(
            "db.write_buffer.backing_off",
            attempt=consecutive_failures,
            delay_s=round(sleep_s, 3),
            requeued=len(items_to_requeue),
        )

        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=sleep_s)
        except TimeoutError:
            pass
