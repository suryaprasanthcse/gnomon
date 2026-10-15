"""Proposal outcomes (master Appendix A). Every rejection is named; nothing is written for any of them."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Code(StrEnum):
    APPLIED = "APPLIED"
    DUPLICATE = "DUPLICATE"
    PID_CONFLICT = "PID_CONFLICT"
    RULE_PINNED = "RULE_PINNED"
    CALLER_DENIED = "CALLER_DENIED"
    INVALID_VIEW = "INVALID_VIEW"
    REJECTED_STALE = "REJECTED_STALE"
    REJECTED_GUARD = "REJECTED_GUARD"
    INDETERMINATE = "INDETERMINATE"
    KERNEL_ERROR = "KERNEL_ERROR"
    SCHEMA_REJECTED = "SCHEMA_REJECTED"


@dataclass(frozen=True)
class Outcome:
    code: Code
    seq: int | None = None
    hash: str | None = None
    detail: dict = field(default_factory=dict)

    @property
    def applied(self) -> bool:
        return self.code is Code.APPLIED
