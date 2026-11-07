"""Sliding-window limiter, in process memory.

Good enough for the single container in the demo. Running several workers needs
a shared store such as Redis, because each process would keep its own counts.
"""

import threading
import time
from collections import defaultdict, deque

from app.errors import AppError


class RateLimiter:
    def __init__(self):
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window_seconds: int) -> None:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= now - window_seconds:
                hits.popleft()
            if len(hits) >= limit:
                retry_after = int(hits[0] + window_seconds - now) + 1
                raise AppError(
                    429,
                    "RATE_LIMITED",
                    "Too many attempts. Please wait and try again.",
                    {"Retry-After": str(retry_after)},
                )
            hits.append(now)

    def clear(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._hits.clear()
            else:
                self._hits.pop(key, None)


limiter = RateLimiter()
