"""Application workflow for explicit projection retries; source/DB adapters are ports."""

from dataclasses import dataclass
from typing import Protocol


class SyncFailure(Exception):
    """Fixed public categories, never transport exception details."""


@dataclass(frozen=True)
class SyncInput:
    snapshot_id: str
    projection: dict
    expected_view: dict


class SyncStore(Protocol):
    def claim(self, snapshot_id: str) -> str | None: ...
    def load(self, snapshot_id: str) -> SyncInput: ...
    def finish(self, snapshot_id: str, token: str, state: str, reason: str | None) -> None: ...
    def status(self, snapshot_id: str) -> dict: ...


class ProjectionTarget(Protocol):
    def apply(self, value: SyncInput) -> None: ...
    def read(self, snapshot_id: str) -> dict | None: ...


def synchronize(snapshot_id: str, store: SyncStore, target: ProjectionTarget):
    """At least once projection import, not exactly once delivery or automatic retry."""
    token = store.claim(snapshot_id)
    if token is None:
        return store.status(snapshot_id)
    state, reason = "failed", "projection_unavailable"
    try:
        value = store.load(snapshot_id)
        target.apply(value)
        if target.read(snapshot_id) != value.expected_view:
            raise SyncFailure("readback_mismatch")
        state, reason = "synced", None
    except SyncFailure as error:
        if str(error) in {"source_changed", "readback_mismatch", "acknowledgment_lost"}:
            reason = str(error)
    except Exception:
        # An unexpected adapter failure leaves a durable failed intent, never a success.
        reason = "projection_unavailable"
    store.finish(snapshot_id, token, state, reason)
    return store.status(snapshot_id)
