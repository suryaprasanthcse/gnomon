"""Canonical state hash (master §12.2): one sorted line per element, keyed by logical identity, hashed with SHA-256."""

import hashlib
import uuid

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

pytestmark = [pytest.mark.gate_g3]

NODES = [
    (["Entity", "Service"], {"uid": "svc:a", "id": "a", "tier": "t1", "criticality": 3}),
    (["Entity", "Service"], {"uid": "svc:b", "id": "b", "tier": "t2", "user_facing": True}),
    (["Entity", "Team"], {"uid": "team:t1", "id": "t1", "name": "Teaém"}),
    (["Head"], {"tenant": "x", "seq": 2, "hash": "0" * 64}),
    (["Rewrite"], {"id": "rw-1", "seq": 1, "view_keys": ["Service:a", "Team:t1"]}),
    (["Rewrite"], {"id": "rw-2", "seq": 2, "view_keys": []}),
    (["Rule"], {"sha": "a" * 64, "name": "Noop", "version": 1}),
    (["KeyVersion"], {"key": "Service:a", "seq": 2, "rw": "rw-2"}),
    (["FactType"], {"rel": "OWNED_BY", "cardinality": "functional"}),
    (["Outbox"], {"pid": "rw-2", "state": "pending", "attempts": 0}),
]
EDGES = [
    ("OWNED_BY", 0, 2, {"fid": "rw-1#0", "vf": 10, "vt": 9007199254740991}),
    ("OWNER_NOW", 0, 2, {"fid": "rw-1#0"}),
    ("DEPENDS_ON", 0, 1, {"fid": "rw-2#0", "hard": True}),
    ("CAUSED_BY", 5, 4, {}),
]


def node_rows(order=None):
    order = range(len(NODES)) if order is None else order
    return [NODES[i] for i in order]


def edge_rows(order=None):
    order = range(len(EDGES)) if order is None else order
    return [(etype, NODES[a][0], NODES[a][1], NODES[b][0], NODES[b][1], props)
            for etype, a, b, props in (EDGES[i] for i in order)]


@pytest.mark.unit
def test_line_format():
    from gnomon.audit.canonical import canonical_lines, hash_lines

    lines = canonical_lines(node_rows(), edge_rows())
    assert lines == sorted(lines)
    assert 'N|svc:a|Entity,Service|{"criticality":3,"id":"a","tier":"t1","uid":"svc:a"}' in lines
    assert 'N|head|Head|{"hash":"' + "0" * 64 + '","seq":2,"tenant":"x"}' in lines
    assert 'N|rw-1|Rewrite|{"id":"rw-1","seq":1,"view_keys":["Service:a","Team:t1"]}' in lines
    assert "N|" + "a" * 64 + '|Rule|{"name":"Noop","sha":"' + "a" * 64 + '","version":1}' in lines
    assert 'N|Service:a|KeyVersion|{"key":"Service:a","rw":"rw-2","seq":2}' in lines
    assert 'N|OWNED_BY|FactType|{"cardinality":"functional","rel":"OWNED_BY"}' in lines
    assert 'N|rw-2|Outbox|{"attempts":0,"pid":"rw-2","state":"pending"}' in lines
    assert 'E|OWNED_BY|svc:a|team:t1|{"fid":"rw-1#0","vf":10,"vt":9007199254740991}' in lines
    assert "E|CAUSED_BY|rw-2|rw-1|{}" in lines
    assert len(lines) == len(NODES) + len(EDGES)
    assert hash_lines(lines) == hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


@pytest.mark.unit
def test_unkeyed_labels_fall_back_to_their_properties():
    from gnomon.audit.canonical import canonical_lines, element_key

    assert element_key(["Mystery"], {"b": 2, "a": 1}) == '?{"a":1,"b":2}'
    assert canonical_lines([(["Mystery"], {"a": 1})], []) == ['N|?{"a":1}|Mystery|{"a":1}']


@pytest.mark.unit
def test_first_difference_names_the_element_and_property():
    from gnomon.audit.canonical import canonical_lines, first_difference

    left = canonical_lines(node_rows(), edge_rows())
    changed = [(labels, {**props, "tier": "t9"} if props.get("uid") == "svc:a" else props) for labels, props in NODES]
    right = canonical_lines(changed, edge_rows())
    assert first_difference(left, left) is None
    a, b = first_difference(left, right)
    assert a.startswith("N|svc:a|") and '"tier":"t1"' in a
    assert b.startswith("N|svc:a|") and '"tier":"t9"' in b
    assert first_difference(left, left[:-1]) == (left[-1], None)
    assert first_difference(left[1:], left) == (None, left[0])


@pytest.mark.unit
@settings(max_examples=100)
@given(st.permutations(range(len(NODES))), st.permutations(range(len(EDGES))))
def test_canonical_hash_order_independent_rows(node_order, edge_order):
    from gnomon.audit.canonical import canonical_lines, hash_lines

    base = hash_lines(canonical_lines(node_rows(), edge_rows()))
    assert hash_lines(canonical_lines(node_rows(node_order), edge_rows(edge_order))) == base


def _create(graph, node_order, edge_order):
    for i in node_order:
        labels, props = NODES[i]
        graph.query(f"CREATE (n:{':'.join(labels)}) SET n = $props SET n.__ix = $ix", {"props": props, "ix": i})
    for i in edge_order:
        etype, a, b, props = EDGES[i]
        graph.query(f"MATCH (a {{__ix: $a}}), (b {{__ix: $b}}) CREATE (a)-[e:{etype}]->(b) SET e = $props",
                    {"a": a, "b": b, "props": props})
    graph.query("MATCH (n) REMOVE n.__ix")


@pytest.mark.integration
@pytest.mark.property
@settings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(st.permutations(range(len(NODES))), st.permutations(range(len(EDGES))))
def test_canonical_hash_order_independent(db, redis_raw, tenant, node_order, edge_order):
    """Property: inserting the same elements in any order gives the same hash (internal ids excluded)."""
    from gnomon.audit.canonical import canonical_lines, hash_lines, state_hash

    key = f"{tenant}:replay:a07-{uuid.uuid4().hex[:6]}"
    graph = db.select_graph(key)
    try:
        _create(graph, node_order, edge_order)
        assert state_hash(graph) == hash_lines(canonical_lines(node_rows(), edge_rows()))
    finally:
        redis_raw.execute_command("GRAPH.DELETE", key)


@pytest.mark.integration
def test_state_hash_ignores_physical_indexes(db, redis_raw, scratch):
    from gnomon.audit.canonical import graph_lines, state_hash

    graph = db.select_graph(scratch)
    before = state_hash(graph)
    graph.query("CREATE INDEX FOR (n:Team) ON (n.name)")
    graph.query("DROP INDEX ON :Service(id)")
    assert state_hash(graph) == before
    lines = graph_lines(graph)
    assert any(line.startswith("N|head|Head|") for line in lines)
    assert not any('"__' in line for line in lines)
    graph.query("MATCH (s:Service {id: 'b'}) SET s.tier = 't9'")
    assert state_hash(graph) != before


@pytest.mark.integration
def test_state_hash_of_an_empty_graph(db):
    from gnomon.audit.canonical import hash_lines, state_hash

    assert state_hash(db.select_graph(f"t:test-absent-{uuid.uuid4().hex[:8]}")) == hash_lines([])
