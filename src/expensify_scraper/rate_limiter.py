from __future__ import annotations

import time
from collections import deque
from threading import Lock


class RateLimiter:
    """Token bucket honoring Expensify limits: 5/10s and 20/60s."""

    def __init__(
        self,
        short_limit: int = 5,
        short_window: float = 10.0,
        long_limit: int = 20,
        long_window: float = 60.0,
    ) -> None:
        self.short_limit = short_limit
        self.short_window = short_window
        self.long_limit = long_limit
        self.long_window = long_window
        self._short_times: deque[float] = deque()
        self._long_times: deque[float] = deque()
        self._lock = Lock()

    def _prune(self, now: float) -> None:
        while self._short_times and now - self._short_times[0] >= self.short_window:
            self._short_times.popleft()
        while self._long_times and now - self._long_times[0] >= self.long_window:
            self._long_times.popleft()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                self._prune(now)
                if (
                    len(self._short_times) < self.short_limit
                    and len(self._long_times) < self.long_limit
                ):
                    self._short_times.append(now)
                    self._long_times.append(now)
                    return
                waits: list[float] = []
                if self._short_times:
                    waits.append(
                        self.short_window - (now - self._short_times[0]) + 0.05
                    )
                if self._long_times:
                    waits.append(self.long_window - (now - self._long_times[0]) + 0.05)
                delay = max(min(waits), 0.05)
            time.sleep(delay)
