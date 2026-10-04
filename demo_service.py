"""Bounded single-event-loop admission for synthetic identities, not HTTP/OAuth auth."""

import asyncio
from collections import deque
from dataclasses import dataclass
import math
import time


@dataclass(frozen=True)
class Limits:
    active: int = 2
    waiting: int = 4
    queue_seconds: float = 2
    task_seconds: float = 5

    def __post_init__(self):
        if (type(self.active) is not int or not 1 <= self.active <= 32
                or type(self.waiting) is not int or not 0 <= self.waiting <= 100
                or any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0
                       for value in (self.queue_seconds, self.task_seconds))):
            raise ValueError("invalid_limits")


class DemoService:
    """Admission mutations contain no awaits; one event loop owns all state.

    Work must be cancellation-cooperative. Non-cooperative blocking SDK calls are
    outside this demo's guarantee. Permits remain held until work cleanup returns.
    """
    def __init__(self, permissions, limits=Limits()):
        self.permissions = {key: frozenset(value) for key, value in permissions.items()}
        self.limits = limits
        self.active = 0
        self.peak_active = 0
        self.users = set()
        self.queue = deque()

    def promote(self):
        while self.queue and self.active < self.limits.active:
            waiter = self.queue.popleft()
            if not waiter.done():
                self.active += 1
                self.peak_active = max(self.active, self.peak_active)
                waiter.set_result(True)

    async def execute(self, user, snapshot_id, work):
        started = time.monotonic()
        def result(status, answer=None, queue_ms=0):
            return {"status": status, "answer": answer, "queue_ms": queue_ms,
                    "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
                    "identity_mode": "synthetic_policy_fixture", "paid_model_calls": 0}

        if snapshot_id not in self.permissions.get(user, ()):
            return result("denied")
        if user in self.users:
            return result("user_busy")
        if self.active >= self.limits.active and len(self.queue) >= self.limits.waiting:
            return result("busy")
        self.users.add(user)
        acquired = False
        waiter = None
        queue_ms = 0
        try:
            if self.active < self.limits.active:
                self.active += 1
                acquired = True
                self.peak_active = max(self.active, self.peak_active)
            else:
                waiter = asyncio.get_running_loop().create_future()
                self.queue.append(waiter)
                try:
                    await asyncio.wait_for(asyncio.shield(waiter),
                                           min(self.limits.queue_seconds, self.limits.task_seconds))
                except TimeoutError:
                    return result("queue_expired")
                acquired = True
            queue_ms = round((time.monotonic() - started) * 1000, 3)
            remaining = self.limits.task_seconds - (time.monotonic() - started)
            if remaining <= 0:
                return result("deadline_exceeded", queue_ms=queue_ms)
            try:
                async with asyncio.timeout(remaining):
                    answer = await work()
                return result("completed", answer, queue_ms)
            except TimeoutError:
                return result("deadline_exceeded", queue_ms=queue_ms)
            except Exception:
                return result("work_failed", queue_ms=queue_ms)
        finally:
            # A promoted waiter may be cancelled before the await resumes. Release
            # its reserved slot, but never steal a slot for an unpromoted waiter.
            if waiter is not None:
                if waiter.done() and not waiter.cancelled() and waiter.result() is True:
                    acquired = True
                if waiter in self.queue:
                    self.queue.remove(waiter)
                if not waiter.done():
                    waiter.cancel()
            if acquired:
                self.active -= 1
            self.users.discard(user)
            self.promote()
