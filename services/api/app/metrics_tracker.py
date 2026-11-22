"""In-process rolling metrics tracker for FSIP API service.

Maintains a sliding time window of request latencies and status codes to compute
p95 latency and error rates for /api/system/status.
"""
from __future__ import annotations

import collections
import time


class RollingMetricsTracker:
    """Sliding-window in-memory metrics tracker."""

    def __init__(self, window_seconds: float = 60.0, max_samples: int = 2000) -> None:
        self.window_seconds = window_seconds
        self.max_samples = max_samples
        self._samples: collections.deque[tuple[float, float, bool]] = collections.deque()

    def record(self, latency_ms: float, is_error: bool) -> None:
        """Record a request observation (timestamp, latency_ms, is_error)."""
        now = time.time()
        self._samples.append((now, latency_ms, is_error))
        self._evict(now)

    def _evict(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()
        while len(self._samples) > self.max_samples:
            self._samples.popleft()

    def get_stats(self) -> tuple[float, float]:
        """Compute (p95_latency_ms, error_rate) over active window."""
        now = time.time()
        self._evict(now)
        if not self._samples:
            return 0.0, 0.0

        latencies = sorted(s[1] for s in self._samples)
        errors = sum(1 for s in self._samples if s[2])

        idx = int(0.95 * (len(latencies) - 1))
        p95 = round(latencies[idx], 2)
        error_rate = round(errors / len(self._samples), 4)
        return p95, error_rate


# Global singleton instance
tracker = RollingMetricsTracker()
