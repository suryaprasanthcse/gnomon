"""Conflict matrix (master §7.6) over the registered rules plus the ClaimTask_v0 lint fixture."""

from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.gate_g2a]

ROOT = Path(__file__).resolve().parents[3]
CLAIMTASK_V0 = ROOT / "tests" / "fixtures" / "rules" / "claimtask_v0.cypher"
FACTS = ["OWNED_BY", "DEPENDS_ON", "DEPLOYED", "REPLICA_OF", "MEMBER_OF"]


def claimtask_v0():
    from gnomon.kernel import macros
    from gnomon.kernel.registry import RuleDef

    return RuleDef(
        name="ClaimTask_v0", version=1, cls="memory", layer="core", caller=("triage",), prelude="P0",
        body=macros.expand(CLAIMTASK_V0.read_text(encoding="utf-8")),
        reads=("Agent", "Agent.id", "Task", "Task.id"), writes=("HOLDS", "Task.state"),
        key_templates=("HOLDS:*:{p.task}", "Task:{p.task}:state", "Task:*"),
    )


@pytest.fixture(scope="module")
def entries():
    from gnomon.footprint.matrix import build
    from gnomon.kernel.registry import load_registry

    registry = load_registry()
    return {(e.a, e.b): e for e in build([*registry.rules(), claimtask_v0()], registry.fact_types())}


def entry(entries, a, b):
    return entries.get((a, b)) or entries[(b, a)]


@pytest.mark.parametrize("rel", FACTS)
def test_assert_x_retract_x_is_guarded(entries, rel):
    e = entry(entries, f"AssertFact.{rel}", f"RetractFact.{rel}")
    assert e.cls == "guarded"
    assert rel in e.overlap


@pytest.mark.parametrize("projected", [("OWNED_BY", "OWNER_NOW"), ("DEPENDS_ON", "DEP_NOW")])
def test_projection_overlap_is_guarded(entries, projected):
    rel, proj = projected
    e = entry(entries, f"AssertFact.{rel}", f"RetractFact.{rel}")
    assert proj in e.overlap


@pytest.mark.parametrize("rel", FACTS)
def test_same_fact_rule_against_itself_is_guarded(entries, rel):
    assert entry(entries, f"AssertFact.{rel}", f"AssertFact.{rel}").cls == "guarded"
    assert entry(entries, f"RetractFact.{rel}", f"RetractFact.{rel}").cls == "guarded"


def test_claimtask_v0_against_itself_is_unguarded(entries):
    e = entry(entries, "ClaimTask_v0", "ClaimTask_v0")
    assert e.cls == "unguarded"
    assert {"HOLDS", "Task.state"} <= set(e.overlap)
    assert set(e.unguarded) == {"HOLDS", "Task.state"}


@pytest.mark.parametrize(
    ("a", "b"),
    [("AssertFact.OWNED_BY", "AssertFact.MEMBER_OF"), ("AssertFact.DEPENDS_ON", "RetractFact.REPLICA_OF"),
     ("DeactivateRule", "AssertFact.OWNED_BY"), ("RegisterAgent", "RegisterRule")],
)
def test_disjoint_footprints_commute(entries, a, b):
    e = entry(entries, a, b)
    assert e.cls == "commutes" and e.overlap == ()


@pytest.mark.parametrize(
    ("a", "b", "element"),
    [("RegisterRule", "DeactivateRule", "Rule.active"), ("RegisterEntity.Service", "AssertFact.OWNED_BY", "Service"),
     ("RegisterEntity.Deploy", "AssertFact.DEPLOYED", "Deploy"), ("RegisterAgent", "AssertFact.MEMBER_OF", "Agent")],
)
def test_registry_and_entity_pairs_are_guarded(entries, a, b, element):
    e = entry(entries, a, b)
    assert e.cls == "guarded", e
    assert element in e.overlap


def test_no_registered_pair_is_unguarded(entries):
    bad = [e for e in entries.values() if e.cls == "unguarded" and "ClaimTask_v0" not in (e.a, e.b)]
    assert bad == []


def test_matrix_is_complete_and_deterministic():
    from gnomon.footprint.matrix import build, to_markdown
    from gnomon.kernel.registry import load_registry

    registry = load_registry()
    rules = registry.rules()
    first = build(rules, registry.fact_types())
    n = len(rules)
    assert len(first) == n * (n + 1) // 2
    assert to_markdown(first) == to_markdown(build(list(reversed(rules)), registry.fact_types()))
    md = to_markdown(first)
    assert md.startswith("# Conflict matrix")
    assert "| AssertFact.OWNED_BY | RetractFact.OWNED_BY |" in md
