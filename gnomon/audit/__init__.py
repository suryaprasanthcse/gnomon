"""The trust layer (master §12, §21): replay, canonical state hash, audits K1–K6 and the mutation suite.

Every check returns a CheckResult: its name and the findings that make it fail (DECISIONS D-28).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CheckResult:
    name: str
    findings: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings

    def as_dict(self) -> dict:
        return {"name": self.name, "ok": self.ok, "findings": list(self.findings)}
