"""Dynamic verification (master §7.5): apply a proposal in sim mode on a scratch copy and check that every
change it made is declared.

1 GRAPH.COPY t:<tenant> t:<tenant>:replay:fpv-<n> (or start from an empty scratch graph, for Genesis)
2 dump the state before, 3 apply in sim mode at (snapshot.pt, snapshot.lc + 1), 4 dump after and diff,
5 every changed element must be a declared write, and every changed instance must be named by one of the
  proposal's instantiated write keys, 6 delete the scratch graph.

The state dump leaves out the kernel's own log (Head, Rewrite, KeyVersion, CAUSED_BY), which Q0 writes for
every rule. Instances are matched to keys by their identity properties (id, uid, name, key); an edge is
matched by its type (or its projection's fact type) and its endpoints, or by a key naming its source node.
The physical-catalogue key family `Index` covers IndexEntry and ConstraintEntry rows.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

import redis

from gnomon.footprint import keys as key_templates
from gnomon.kernel.outcomes import Outcome
from gnomon.kernel.registry import Registry, RuleDef
from gnomon.kernel.service import KernelService, Proposal

KERNEL_LOG_LABELS = frozenset({"Head", "Rewrite", "KeyVersion"})
KERNEL_LOG_TYPES = frozenset({"CAUSED_BY"})
IDENT_PROPS = ("id", "uid", "name", "key", "pid")
KEY_ALIASES = {"Index": {"IndexEntry", "ConstraintEntry"}}


@dataclass(frozen=True)
class Change:
    kind: str  # node | edge
    op: str  # create | delete | update
    name: tuple[str, ...]  # node labels, or (edge type,)
    prop: str | None = None
    ident: Mapping = field(default_factory=dict)
    src: Mapping = field(default_factory=dict)
    dst: Mapping = field(default_factory=dict)
    src_labels: tuple[str, ...] = ()


@dataclass
class Verification:
    outcome: Outcome
    changes: list[Change]
    violations: list[str]
    write_keys: list[str]


def _ident(props: Mapping) -> dict:
    return {k: props[k] for k in IDENT_PROPS if props.get(k) is not None}


def canonical_state(graph, client: redis.Redis) -> dict:
    """{('n', id): (labels, props)} and {('e', id): (type, props, src_ident, dst_ident)} without the kernel log."""
    if not client.exists(graph.name):
        return {}
    state = {}
    for nid, labels, props in graph.ro_query("MATCH (n) RETURN ID(n), labels(n), properties(n)").result_set:
        if not KERNEL_LOG_LABELS & set(labels):
            state[("n", nid)] = (tuple(sorted(labels)), dict(props))
    rows = graph.ro_query("MATCH (a)-[e]->(b) RETURN ID(e), type(e), properties(e), properties(a), properties(b), "
                          "labels(a)")
    for eid, etype, props, a, b, a_labels in rows.result_set:
        if etype not in KERNEL_LOG_TYPES:
            state[("e", eid)] = (etype, dict(props), _ident(a), _ident(b), tuple(sorted(a_labels)))
    return state


def diff(before: dict, after: dict) -> list[Change]:
    changes = []
    for key in sorted(set(before) | set(after), key=repr):
        old, new = before.get(key), after.get(key)
        if old == new:
            continue
        kind = "node" if key[0] == "n" else "edge"
        current = new if new is not None else old
        name = current[0] if kind == "node" else (current[0],)
        extra = {} if kind == "node" else {"src": current[2], "dst": current[3], "src_labels": current[4]}
        ident = _ident(current[1])
        if old is None or new is None:
            changes.append(Change(kind, "create" if old is None else "delete", name, None, ident, **extra))
            continue
        for p in sorted(set(old[1]) | set(new[1])):
            if old[1].get(p) != new[1].get(p):
                changes.append(Change(kind, "update", name, p, ident, **extra))
    return changes


def _names(key: str, root: str, values) -> bool:
    """`<root>:*`, or `<root>:<v>` / `<root>:<v>:…` for one of the instance's identity values (uids keep their ':')."""
    if key in (root, f"{root}:*"):
        return True
    return any(key == f"{root}:{v}" or key.startswith(f"{root}:{v}:") for v in map(str, values))


def _key_covers(key: str, change: Change, projections: Mapping[str, str]) -> bool:
    root = key.split(":", 1)[0]
    roots = {root} | KEY_ALIASES.get(root, set())
    if change.kind == "node":
        return bool(roots & set(change.name)) and _names(key, root, change.ident.values())
    etype = change.name[0]
    if etype in roots or projections.get(etype) in roots:
        return _names(key, root, change.src.values()) or \
            any(key == f"{root}:*:{v}" for v in map(str, change.dst.values()))
    # a structural edge (HAS_SKILL, ON, CITES…) is covered by a key of the node it hangs from
    if change.src_labels and not roots & set(change.src_labels):
        return False
    return _names(key, root, change.src.values()) and (bool(change.src_labels) or key != f"{root}:*")


def check(rule: RuleDef, write_keys: Iterable[str], changes: Iterable[Change],
          fact_types: Iterable[Mapping] = ()) -> list[str]:
    projections = {ft["projection"]: ft["rel"] for ft in fact_types if ft.get("projection")}
    declared = set(rule.writes)
    write_keys = list(write_keys)
    violations = []
    for c in changes:
        what = "/".join(c.name) + (f".{c.prop}" if c.prop else "")
        if c.op == "update":
            if not any(f"{n}.{c.prop}" in declared or n in declared for n in c.name):
                violations.append(f"{rule.name}: {'.'.join((c.name[0], c.prop))} changed but is not declared in writes")
        else:
            missing = [n for n in c.name if n not in declared]
            if missing:
                violations.append(f"{rule.name}: {', '.join(missing)} {c.op}d but not declared in writes")
        if not any(_key_covers(k, c, projections) for k in write_keys):
            violations.append(f"{rule.name}: {c.op} of {what} {dict(c.ident) or dict(c.src)} is covered by no write "
                              f"key in {write_keys}")
    return violations


def verify(db, client: redis.Redis, source_key: str | None, scratch_key: str, registry: Registry, *, rule: str,
           p: Mapping, pid: str, agent: str, view_keys: Iterable[str] = ()) -> Verification:
    graph = db.select_graph(scratch_key)
    try:
        if source_key is not None:
            client.execute_command("GRAPH.COPY", source_key, scratch_key)
        before = canonical_state(graph, client)
        head = graph.ro_query("MATCH (h:Head) RETURN h.seq, h.pt, h.lc").result_set if client.exists(scratch_key) \
            else []
        seq, pt, lc = head[0] if head else (0, 0, 0)
        outcome = KernelService(graph, registry).submit(Proposal(
            rule=rule, p=dict(p), pid=pid, agent=agent, read_seq=seq, view_keys=tuple(view_keys), mode="sim",
            rec_pt=pt, rec_lc=lc + 1,
        ))
        changes = diff(before, canonical_state(graph, client))
        definition = registry.get(rule)
        try:
            write_keys = key_templates.instantiate(definition.key_templates, p, pid)
        except key_templates.KeyTemplateError:
            write_keys = []
        violations = check(definition, write_keys, changes, registry.fact_types())
        return Verification(outcome, changes, violations, write_keys)
    finally:
        if client.exists(scratch_key):
            client.execute_command("GRAPH.DELETE", scratch_key)
