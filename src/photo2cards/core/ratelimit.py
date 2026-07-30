"""Client-side throttle.

The free tier's limit is per-minute, and a user dropping ten photos on the add-on
will otherwise fire ten requests instantly and collect nine 429s. Spacing the
calls out locally is cheaper than retrying after the fact.
"""

from __future__ import annotations

import threading
import time
from collections import deque


class RateLimiter:
    """Sliding-window limiter: at most `per_minute` acquisitions in any 60s span.

    Thread-safe, because generation runs on Anki's background thread pool and a
    future version may fan out across several.
    """

    def __init__(self, per_minute: int, window: float = 60.0) -> None:
        self.per_minute = max(1, int(per_minute))
        self.window = window
        self._hits: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self, should_cancel=None) -> None:
        """Block until a slot is free.

        `should_cancel` is an optional zero-arg callable; when it returns True we
        stop waiting and raise, so a user cancelling a long batch isn't stuck
        behind the throttle.
        """
        while True:
            with self._lock:
                now = time.monotonic()
                while self._hits and now - self._hits[0] >= self.window:
                    self._hits.popleft()
                if len(self._hits) < self.per_minute:
                    self._hits.append(now)
                    return
                wait = self.window - (now - self._hits[0])

            if should_cancel is not None and should_cancel():
                raise InterruptedError("cancelled while waiting for rate limit")
            time.sleep(min(wait, 0.5))
