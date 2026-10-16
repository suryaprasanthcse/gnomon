"""Switching a tenant's federation on (master §14, §17): the T-D rule set, registered by logged rewrites.

Activation is M3 (RegisterRule) for X0-X6 and X3/X4's receive and ack rules, and M6 (RegisterAgent) for the relay, so
the log alone shows when and by whom the layer was switched on. Channels are then opened with X0 (OpenChannel).
"""

from __future__ import annotations

from gnomon.kernel.registry import RuleDef, layer_rules, load_registry, register_rule_params
from gnomon.kernel.service import KernelHandle, KernelService

LAYER = "TD"
RELAY_ROLE = "relay"


def td_rules() -> list[RuleDef]:
    return layer_rules(LAYER)


def td_registry():
    """The Core registry plus the T-D rules: what a federated tenant's kernel service knows how to bind."""
    return load_registry().with_rules(*td_rules())


def attach(graph) -> KernelHandle:
    """A kernel handle on `graph` that can submit T-D rules (once they are registered in the graph's log)."""
    return KernelHandle(graph, td_registry())


def peering_key(a: str, b: str) -> str:
    """The key of the sys Peering of two tenants (a < b)."""
    return "|".join(sorted((a, b)))


def activate(kernel: KernelHandle, *, operator: str, relay: str, at: int, step: int = 10) -> int:
    """Registers every T-D rule (M3) and the relay agent (M6) in replay mode at `at`, `at + step`, ... Returns the last time used."""
    kernel.registry = td_registry()
    kernel.service = KernelService(kernel.graph, kernel.registry)
    pt = at
    for rule in td_rules():
        out = kernel.submit("RegisterRule", register_rule_params(rule), pid=f"m3-{rule.name}", agent=operator,
                            mode="replay", rec_pt=pt, rec_lc=0)
        if not out.applied:
            raise RuntimeError(f"registering {rule.name} was refused: {out.code} {out.detail}")
        pt += step
    out = kernel.submit("RegisterAgent", {"op": "register", "id": relay, "role": RELAY_ROLE, "skills": []},
                        pid=f"m6-{relay}", agent=operator, mode="replay", rec_pt=pt, rec_lc=0)
    if not out.applied:
        raise RuntimeError(f"registering the relay was refused: {out.code} {out.detail}")
    return pt
