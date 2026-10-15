"""The §7.5 subset check on its own: changed elements ⊆ declared writes, changed instances ⊆ write keys."""

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.gate_g2a]


def rule(**kw):
    from gnomon.kernel.registry import RuleDef

    base = dict(name="R", version=1, cls="memory", layer="core", caller=("operator",), prelude="P0", body="")
    base.update(kw)
    return RuleDef(**base)


def node(op, labels, ident, prop=None):
    from gnomon.footprint.verify import Change

    return Change(kind="node", op=op, name=tuple(labels), prop=prop, ident=ident)


def edge(op, rel, src, dst, prop=None):
    from gnomon.footprint.verify import Change

    return Change(kind="edge", op=op, name=(rel,), prop=prop, ident={}, src=src, dst=dst)


def check(r, keys, changes):
    from gnomon.footprint.verify import check

    return check(r, keys, changes, fact_types=[{"rel": "OWNED_BY", "projection": "OWNER_NOW"}])


def test_declared_and_keyed_changes_pass():
    r = rule(writes=("Rule.active", "Rule.deactivated_seq"))
    changes = [node("update", ["Rule"], {"name": "X"}, "active"), node("update", ["Rule"], {"name": "X"},
                                                                       "deactivated_seq")]
    assert check(r, ["Rule:X", "Rule:*"], changes) == []


def test_undeclared_property_write_is_a_violation():
    r = rule(writes=("Rule.active",))
    v = check(r, ["Rule:X"], [node("update", ["Rule"], {"name": "X"}, "deactivated_seq")])
    assert v and "Rule.deactivated_seq" in v[0] and "not declared" in v[0]


def test_undeclared_label_on_created_node_is_a_violation():
    r = rule(writes=("Service",))
    v = check(r, ["Service:web:state"], [node("create", ["Service", "Entity"], {"id": "web", "uid": "svc:web"})])
    assert v and "Entity" in v[0]


def test_instance_outside_the_write_keys_is_a_violation():
    r = rule(writes=("Rule.active",))
    v = check(r, ["Rule:X"], [node("update", ["Rule"], {"name": "Y"}, "active")])
    assert v and "no write key" in v[0]


def test_phantom_key_covers_any_instance():
    r = rule(writes=("Rule",))
    assert check(r, ["Rule:*"], [node("create", ["Rule"], {"name": "Z"})]) == []


def test_edges_are_matched_by_type_source_and_target():
    r = rule(writes=("OWNED_BY", "OWNED_BY.te", "OWNER_NOW"))
    src, dst = {"uid": "svc:a", "id": "a"}, {"uid": "team:t1", "id": "t1"}
    changes = [edge("update", "OWNED_BY", src, dst, "te"), edge("create", "OWNED_BY", src, dst),
               edge("delete", "OWNER_NOW", src, dst)]
    assert check(r, ["OWNED_BY:svc:a:*", "OWNED_BY:*:team:t1", "OWNED_BY:*"], changes) == []
    assert check(r, ["OWNED_BY:svc:b:*"], changes[:1])  # another subject's key does not cover it


def test_structural_edge_from_a_keyed_node_is_covered():
    r = rule(writes=("Agent", "Entity", "HAS_SKILL"))
    a = {"id": "inv-1", "uid": "agent:inv-1"}
    changes = [node("create", ["Agent", "Entity"], a), edge("create", "HAS_SKILL", a, {"id": "ops", "uid": "skill:ops"})]
    assert check(r, ["Agent:*", "Agent:inv-1:state"], changes) == []
