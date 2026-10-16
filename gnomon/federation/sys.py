"""The registry graph `t:_sys` (master §17.2): S0 genesis, S1 RegisterTenant, S2 PeerTenants, and tenant anchors.

`t:_sys` is a kernel graph of its own, with its own seq, HLC and chain. Its rule set is S1, S2 and the meta rules
M3, M4 and M6 only. A tenant's R0 records the sys rewrite that registered it as `Head.sys_anchor`; K9f checks it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

import redis

from gnomon.bootstrap.genesis import bootstrap
from gnomon.kernel.jcs import canonicalize
from gnomon.kernel.outcomes import Outcome
from gnomon.kernel.registry import Registry, layer_rules, load_registry
from gnomon.kernel.service import KernelHandle

SYS_TENANT = "_sys"
SYS_KEY = "t:_sys"
SYS_RULES = ("Genesis", "RegisterRule", "DeactivateRule", "RegisterAgent")
SYS_AGENTS: tuple[Mapping, ...] = (
    {"id": "kernel", "role": "kernel", "skills": []},
    {"id": "ops-1", "role": "operator", "skills": []},
)
ANCHOR_FIELDS = ("sys_seq", "sys_hash", "sys_rw")


def sys_registry() -> Registry:
    """S0's rule set: Genesis, M3, M4, M6 from the core registry, and S1 and S2 from the SYS layer."""
    core = load_registry()
    return Registry([*(core.get(name) for name in SYS_RULES), *layer_rules("SYS")], ())


def bootstrap_sys(db, client: redis.Redis, key: str = SYS_KEY, *, agents: Sequence[Mapping] = SYS_AGENTS,
                  pid: str = "genesis", mode: str = "live", rec_pt: int | None = None,
                  rec_lc: int | None = None) -> Outcome:
    """S0: bootstrap DDL, then R0 with tenant `_sys`."""
    return bootstrap(db.select_graph(key), client, sys_registry(), tenant=SYS_TENANT, agents=agents, skills=[],
                     pid=pid, mode=mode, rec_pt=rec_pt, rec_lc=rec_lc)


def sys_handle(db, key: str = SYS_KEY) -> KernelHandle:
    return KernelHandle(db.select_graph(key), sys_registry())


def anchor_of(sys_kernel: KernelHandle, tenant: str) -> dict | None:
    """The sys rewrite that registered `tenant` (its S1), as the anchor a tenant's R0 records."""
    rows = sys_kernel.read(
        "MATCH (t:Tenant {id: $id}) MATCH (r:Rewrite) WHERE r.id = t.rw RETURN r.seq, r.hash, r.id", {"id": tenant})
    if not rows:
        return None
    seq, digest, rid = rows[0]
    return {"sys_seq": seq, "sys_hash": digest, "sys_rw": rid}


def anchor_param(anchor: Mapping | None) -> str | None:
    """`$p.sys_anchor` of R0: a map is not a storable property (FACTS S1), so the anchor is its canonical JSON."""
    return None if anchor is None else canonicalize(dict(anchor))


def parse_anchor(text: str | None) -> dict | None:
    """The anchor stored in Head.sys_anchor, or None when absent or malformed."""
    if not text:
        return None
    try:
        anchor = json.loads(text)
    except ValueError:
        return None
    if not isinstance(anchor, dict) or set(anchor) != set(ANCHOR_FIELDS):
        return None
    return anchor
