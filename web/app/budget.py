"""In-process daily budget and per-IP live-run limit.

Cached audits do not spend either. There is no process-wide job lock:
two visitors can audit at the same time.
"""

from __future__ import annotations

import asyncio
import time
from datetime import date


class Budget:
    def __init__(self, max_daily_usd: float) -> None:
        self.max_daily_usd = max_daily_usd
        self.spent = 0.0
        self.day = date.today()
        self._lock = asyncio.Lock()

    def _roll(self) -> None:
        today = date.today()
        if today != self.day:
            self.day = today
            self.spent = 0.0

    async def try_reserve(self, amount: float) -> bool:
        async with self._lock:
            self._roll()
            if amount <= 0:
                return True
            if self.spent + amount > self.max_daily_usd + 1e-9:
                return False
            self.spent += amount
            return True

    async def reconcile(self, reserved: float, actual: float) -> None:
        """Replace a reserve with the receipt cost. Actual may be lower or higher."""
        async with self._lock:
            self._roll()
            self.spent += actual - reserved
            if self.spent < 0:
                self.spent = 0.0

    async def refund(self, amount: float) -> None:
        await self.reconcile(amount, 0.0)

    def snapshot(self) -> dict:
        self._roll()
        return {
            "spent_usd": round(self.spent, 6),
            "max_daily_usd": self.max_daily_usd,
            "remaining_usd": round(max(0.0, self.max_daily_usd - self.spent), 6),
        }


class RateLimiter:
    def __init__(self, limit: int, window_s: int = 3600) -> None:
        self.limit = limit
        self.window_s = window_s
        self._hits: dict[str, list[float]] = {}
        self._lock = asyncio.Lock()

    async def allow(self, key: str) -> bool:
        now = time.monotonic()
        async with self._lock:
            hits = [stamp for stamp in self._hits.get(key, []) if now - stamp < self.window_s]
            if len(hits) >= self.limit:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            return True
