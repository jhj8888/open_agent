"""Small, versioned Temporal payloads, independent of model and sandbox libraries."""

from dataclasses import dataclass
import re
from uuid import UUID

TERMINAL = frozenset({"succeeded", "failed", "timed_out", "cancelled"})


@dataclass(frozen=True)
class RunRef:
    tenant: str
    environment: str
    run_id: str

    def __post_init__(self) -> None:
        if (
            not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", self.tenant)
            or self.environment not in {"test", "prod"}
            or str(UUID(self.run_id)) != self.run_id
        ):
            raise ValueError("Invalid run reference")


@dataclass(frozen=True)
class RunResult:
    state: str

    def __post_init__(self) -> None:
        if self.state not in TERMINAL | {"queued"}:
            raise ValueError("Invalid durable run result")


@dataclass(frozen=True)
class ReconcileRequest:
    run: RunRef
    reason: str

    def __post_init__(self) -> None:
        if self.reason not in {"cancel_requested", "outcome_unknown"}:
            raise ValueError("Invalid reconciliation reason")
