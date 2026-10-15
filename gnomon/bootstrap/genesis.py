"""R0 Genesis (master §9.1): the params that seed a tenant's kernel graph, and the bootstrap sequence.

All validation happens in PG, inside the apply query, before the first write (§6.3). The params carry
the kernel source and its sha, every rule with its def_json and sha, the fact types (§3.3), skills,
agents and the base index/constraint catalogue (§3.5).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import redis

from gnomon.bootstrap import ddl
from gnomon.kernel import composer
from gnomon.kernel.outcomes import Outcome
from gnomon.kernel.registry import Registry
from gnomon.kernel.service import KernelService, Proposal

KERNEL_AGENT = "kernel"

FACT_TYPES: tuple[dict, ...] = (
    {"rel": "OWNED_BY", "s_labels": ["Service"], "o_labels": ["Team"], "cardinality": "functional",
     "projection": "OWNER_NOW", "attrs": []},
    {"rel": "DEPENDS_ON", "s_labels": ["Service"], "o_labels": ["Service", "ExternalService"], "cardinality": "set",
     "projection": "DEP_NOW", "attrs": ["hard"]},
    {"rel": "DEPLOYED", "s_labels": ["Service"], "o_labels": ["Deploy"], "cardinality": "functional",
     "projection": None, "attrs": []},
    {"rel": "REPLICA_OF", "s_labels": ["Service"], "o_labels": ["Service"], "cardinality": "functional",
     "projection": None, "attrs": []},
    {"rel": "MEMBER_OF", "s_labels": ["Agent"], "o_labels": ["Team"], "cardinality": "set", "projection": None,
     "attrs": []},
    {"rel": "ROOT_CAUSE", "s_labels": ["Incident"], "o_labels": ["Deploy", "Service"], "cardinality": "set",
     "projection": None, "attrs": ["by", "confidence"]},
    {"rel": "CONSUMES", "s_labels": ["Consumer"], "o_labels": ["Service"], "cardinality": "set",
     "projection": "CONSUMES_NOW", "attrs": ["hard"]},
)


def build_params(registry: Registry, *, tenant: str, agents: Sequence[Mapping], skills: Sequence[str],
                 fact_types: Sequence[Mapping] = FACT_TYPES, sys_anchor: str | None = None) -> dict:
    source = composer.kernel_source()
    return {
        "tenant": tenant,
        "kernel_sha": composer.kernel_sha(),
        "kernel_src": source,
        "sys_anchor": sys_anchor,
        "rules": [rule.to_param() for rule in registry.rules()],
        "fact_types": [dict(ft) for ft in fact_types],
        "skills": list(skills),
        "agents": [{"id": a["id"], "role": a["role"], "skills": list(a.get("skills", []))} for a in agents],
        "base_indexes": [i.as_param() for i in ddl.BASE_INDEXES],
        "base_constraints": [c.as_param() for c in ddl.BASE_CONSTRAINTS],
    }


def run_genesis(service: KernelService, params: dict, *, pid: str = "genesis", mode: str = "live",
                rec_pt: int | None = None, rec_lc: int | None = None) -> Outcome:
    return service.submit(Proposal(rule="Genesis", p=params, pid=pid, agent=KERNEL_AGENT, read_seq=0, view_keys=(),
                                   mode=mode, rec_pt=rec_pt, rec_lc=rec_lc))


def bootstrap(graph, client: redis.Redis, registry: Registry, *, tenant: str, agents: Sequence[Mapping],
              skills: Sequence[str], pid: str = "genesis", mode: str = "live", rec_pt: int | None = None,
              rec_lc: int | None = None, sys_anchor: str | None = None) -> Outcome:
    """Bootstrap DDL (waits until operational), then R0."""
    ddl.apply(graph, client)
    params = build_params(registry, tenant=tenant, agents=agents, skills=skills, sys_anchor=sys_anchor)
    return run_genesis(KernelService(graph, registry), params, pid=pid, mode=mode, rec_pt=rec_pt, rec_lc=rec_lc)
