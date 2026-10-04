"""Framework-independent local identity and read-authorization port."""

from dataclasses import dataclass
from typing import Protocol


class AuthError(Exception):
    """Fixed safe reason; never include credentials, IDs or storage exceptions."""


@dataclass(frozen=True)
class Principal:
    user_id: str
    tenant_id: str
    role: str


class Authorization(Protocol):
    def authorize(self, token: str, snapshot_id: str, action: str = "read_evidence") -> Principal: ...
