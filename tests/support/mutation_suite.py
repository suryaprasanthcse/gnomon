"""The full mutation suite MU1-MU32 as one report (A16, master §21.2): which checks fired for each mutation.

A mutation is applied to a scratch copy (`<graph>:replay:mut-<n>`), the audits are run on the copy, and the checks that
failed are recorded next to the ones the catalogue predicts. A mutation is caught when every predicted check fired; it
fails the suite when one did not. Other checks may fire too, and the report lists them. The mutations live in several
worlds (a graph needs the element a mutation corrupts), so the suite is assembled from the worlds the tests build.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

CORE_CHECKS = ["K1", "K2", "K3", "K4", "K5", "K6", "K7", "K8"]


@dataclass
class Result:
    mu: str
    title: str
    expected: tuple[str, ...]
    fired: list[str]
    world: str

    @property
    def caught(self) -> bool:
        return set(self.expected) <= set(self.fired)

    @property
    def extra(self) -> list[str]:
        return sorted(set(self.fired) - set(self.expected))


@dataclass
class Suite:
    results: dict[str, Result] = field(default_factory=dict)

    def add(self, result: Result) -> Result:
        assert result.mu not in self.results, f"{result.mu} was run twice"
        self.results[result.mu] = result
        return result

    def missed(self) -> list[str]:
        return [mu for mu, r in self.results.items() if not r.caught]

    def table(self) -> list[str]:
        return [f"{r.mu:5} {r.world:11} expected {','.join(r.expected):14} fired {','.join(r.fired) or '-':32} "
                f"{'caught' if r.caught else 'MISSED'}  {r.title}" for r in self.results.values()]

    def as_dict(self) -> dict:
        return {mu: {"expected": list(r.expected), "fired": r.fired, "caught": r.caught, "world": r.world}
                for mu, r in self.results.items()}


def _delete(client, key: str) -> None:
    if client.exists(key):
        client.execute_command("GRAPH.DELETE", key)


def run_on_copy(db, client, source: str, mu: str, world: str, *, checks=CORE_CHECKS, settle: float = 0.0,
                restore=None) -> Result:
    """Copy `source`, apply `mu` to the copy, audit the copy with `checks`; the copy is deleted either way."""
    from gnomon.audit.mutations import apply_mutation
    from gnomon.audit.runner import audit

    key = f"{source}:replay:mut-{mu[2:]}"
    _delete(client, key)
    client.execute_command("GRAPH.COPY", source, key)
    try:
        mutation = apply_mutation(db, client, key, mu)
        if settle:
            time.sleep(settle)
        fired = audit(db, client, key, checks).failed
        return Result(mu, mutation.title, mutation.expected, fired, world)
    finally:
        if restore is not None:
            restore()
        _delete(client, key)


def run_federated(aw, mu: str, victim: str, world: str = "federation") -> Result:
    """The same for a federation: all four graphs are copied, `mu` corrupts the victim tenant, K9 and K4 are read."""
    from gnomon.audit.mutations import apply_mutation
    from gnomon.audit.runner import audit, audit_federation
    from tests.integration.test_a13_federation import TENANTS

    live = {**{t: aw.k[t].graph.name for t in TENANTS}, "sys": aw.sysk.graph.name}
    scratch = {name: f"{key}:replay:mut-{mu[2:]}" for name, key in live.items()}
    try:
        for name, key in scratch.items():
            _delete(aw.client, scratch[name])
            aw.client.execute_command("GRAPH.COPY", live[name], key)
        mutation = apply_mutation(aw.db, aw.client, scratch[victim], mu)
        report = audit_federation(aw.db, aw.client, {t: scratch[t] for t in TENANTS}, scratch["sys"])
        replay = audit(aw.db, aw.client, scratch[victim], ["K4"])
        return Result(mu, mutation.title, mutation.expected, sorted({*report.failed, *replay.failed}), world)
    finally:
        for key in scratch.values():
            _delete(aw.client, key)
