"""K8 · the physical catalogue equals the logical one (master §16.14, §21.1).

`CALL db.indexes()` and `CALL db.constraints()` on the graph are compared, in both directions, with the logged
IndexEntry and ConstraintEntry rows. A logged operational index that is missing, or an operational index nobody logged,
is a finding. An entry still `requested` or `retiring` is in flight (the reconciler has not confirmed it yet) and an
index still being built counts as present, so a settle in progress is never reported (DECISIONS D-61).
"""

from __future__ import annotations

from gnomon.audit import CheckResult

ENTITY = {"NODE": "node", "RELATIONSHIP": "edge"}
INDEXES = ("CALL db.indexes() YIELD label, properties, entitytype, status "
           "RETURN label, properties, entitytype, status")
CONSTRAINTS = ("CALL db.constraints() YIELD type, label, properties, entitytype, status "
               "RETURN type, label, properties, entitytype, status")
INDEX_ENTRIES = "MATCH (x:IndexEntry) RETURN x.key AS key, x.state AS state ORDER BY key"
CONSTRAINT_ENTRIES = "MATCH (x:ConstraintEntry) RETURN x.key AS key, x.state AS state ORDER BY key"
IN_FLIGHT = ("requested", "retiring")


def physical_indexes(graph) -> dict[str, str]:
    return {f"{ENTITY[etype]}:{label}:{prop}": status
            for label, props, etype, status in graph.ro_query(INDEXES).result_set for prop in props}


def physical_constraints(graph) -> dict[str, str]:
    return {f"{ctype.lower()}:{ENTITY[etype]}:{label}:{','.join(props)}": status
            for ctype, label, props, etype, status in graph.ro_query(CONSTRAINTS).result_set}


def check(graph) -> CheckResult:
    findings: list[dict] = []
    logical = dict(graph.ro_query(INDEX_ENTRIES).result_set)
    physical = physical_indexes(graph)
    for key in sorted(logical):
        if logical[key] == "operational" and physical.get(key) in (None, "FAILED"):
            findings.append({"problem": "index_missing_physical", "key": key})
    for key in sorted(physical):
        if physical[key] == "OPERATIONAL" and logical.get(key) not in ("operational", *IN_FLIGHT):
            findings.append({"problem": "index_unlogged", "key": key})
    logical_c = dict(graph.ro_query(CONSTRAINT_ENTRIES).result_set)
    physical_c = physical_constraints(graph)
    for key in sorted(logical_c):
        if logical_c[key] == "operational" and physical_c.get(key) is None:
            findings.append({"problem": "constraint_missing_physical", "key": key})
    for key in sorted(physical_c):
        if physical_c[key] == "OPERATIONAL" and logical_c.get(key) != "operational":
            findings.append({"problem": "constraint_unlogged", "key": key})
    return CheckResult("K8", findings)
