"""Lints L1–L8 (master §5.5): one passing and one failing fixture each, ClaimTask_v0, and the registered core."""

import dataclasses
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.gate_g2a]

ROOT = Path(__file__).resolve().parents[3]
CASES = ROOT / "tests" / "fixtures" / "rules" / "lint_cases"
CLAIMTASK_V0 = ROOT / "tests" / "fixtures" / "rules" / "claimtask_v0.cypher"
LINTS = [f"L{n}" for n in range(1, 9)]


def case(name):
    from gnomon.kernel.registry import load_rule_file

    return load_rule_file(CASES / f"{name}.yaml")


def result(rule, lint_id):
    from gnomon.footprint.lints import lint

    report = lint(rule)
    [r] = [r for r in report.results if r.id == lint_id]
    return r


@pytest.mark.parametrize("lint_id", LINTS)
def test_positive_fixture_passes(lint_id):
    r = result(case(f"{lint_id.lower()}_pass"), lint_id)
    assert r.ok, r.messages
    assert r.messages == []


@pytest.mark.parametrize("lint_id", LINTS)
def test_negative_fixture_fails(lint_id):
    r = result(case(f"{lint_id.lower()}_fail"), lint_id)
    assert not r.ok
    assert r.messages and all(m.startswith(lint_id) for m in r.messages)


@pytest.mark.parametrize("lint_id", LINTS)
def test_positive_fixtures_pass_every_lint(lint_id):
    from gnomon.footprint.lints import lint

    report = lint(case(f"{lint_id.lower()}_pass"))
    assert report.ok, [m for r in report.results for m in r.messages]


def claimtask_v0():
    from gnomon.kernel import macros
    from gnomon.kernel.registry import RuleDef

    return RuleDef(
        name="ClaimTask_v0", version=1, cls="memory", layer="core", caller=("investigator", "triage"), prelude="P0",
        body=macros.expand(CLAIMTASK_V0.read_text(encoding="utf-8")),
        reads=("Agent", "Agent.id", "Task", "Task.id"), writes=("HOLDS", "Task.state"),
        key_templates=("HOLDS:*:{p.task}", "Task:{p.task}:state", "Task:*"),
    )


def test_claimtask_v0_refused_by_L1():
    r = result(claimtask_v0(), "L1")
    assert not r.ok
    assert r.messages == ["L1 unguarded write: HOLDS (HOLDS:*:{p.task}), Task.state (Task:{p.task}:state)"]


def test_claimtask_v0_fails_only_L1():
    from gnomon.footprint.lints import lint

    report = lint(claimtask_v0())
    assert [r.id for r in report.results if not r.ok] == ["L1"]


def test_every_registered_rule_passes_all_lints():
    from gnomon.footprint.lints import lint
    from gnomon.kernel.registry import load_registry

    for rule in load_registry().rules():
        report = lint(rule)
        assert report.ok, (rule.name, [m for r in report.results for m in r.messages])


@pytest.mark.parametrize(
    "body",
    [
        "MATCH (t:Task {id: $p.task})\nWHERE t.state = 'open'\nWITH KCTX, t\nSET t.state = 'claimed', t.r = rand()\nWITH KCTX\n",
        "MATCH (t:Task {id: $p.task})\nWHERE t.state = 'open'\nWITH KCTX, t\nSET t.state = randomUUID()\nWITH KCTX\n",
        "MATCH (s:Service {id: $p.s})\nOPTIONAL MATCH (s)<-[:ON]-(a:Alert)\nWITH KCTX, s, head(collect(a)) AS first\n"
        "WHERE s.health = 'ok'\nFOREACH (x IN CASE WHEN first IS NULL THEN [] ELSE [first] END | SET x.state = 'seen')\n"
        "WITH KCTX\n",
    ],
    ids=["rand", "randomUUID", "unguarded-positional-collect"],
)
def test_l2_other_sources_of_nondeterminism(body):
    rule = dataclasses.replace(case("l2_pass"), body=body)
    assert not result(rule, "L2").ok


def test_l2_allows_count_guarded_or_anchored_picks():
    counted = ("MATCH (s:Service {id: $p.s})\nOPTIONAL MATCH (s)<-[:ON]-(a:Alert)\n"
               "WITH KCTX, s, count(a) AS n, head(collect(a)) AS only\nWHERE n = 1 AND s.health = 'ok'\n"
               "SET only.state = 'seen'\nWITH KCTX\n")
    anchored = ("MATCH (s:Service {id: $p.s})\nOPTIONAL MATCH (s)<-[on:ON {fid: $p.fid}]-(a:Alert)\n"
                "WITH KCTX, s, head(collect(on)) AS e\nWHERE s.health = 'ok'\n"
                "FOREACH (x IN CASE WHEN e IS NULL THEN [] ELSE [e] END | DELETE x)\nWITH KCTX\n")
    for body in (counted, anchored):
        assert result(dataclasses.replace(case("l2_pass"), body=body), "L2").ok, body


def test_l3_body_that_can_end_with_many_rows():
    body = ("MATCH (s:Service {id: $p.s})\nWHERE s.health = 'ok'\nUNWIND $p.tags AS tag\nWITH KCTX, s, tag\n"
            "SET s.tag = tag\nWITH KCTX\n")
    r = result(dataclasses.replace(case("l3_pass"), body=body), "L3")
    assert not r.ok and any("more than one row" in m for m in r.messages)


def test_l3_unanchored_optional_match():
    body = ("MATCH (t:Task {id: $p.task})\nOPTIONAL MATCH (b:Blocker)\nWITH KCTX, t, count(b) AS n\n"
            "WHERE n = 0 AND t.state = 'open'\nSET t.state = 'claimed'\nWITH KCTX\n")
    r = result(dataclasses.replace(case("l3_pass"), body=body), "L3")
    assert not r.ok and any("Blocker" in m for m in r.messages)


def test_l4_reports_undeclared_writes_too():
    rule = dataclasses.replace(case("l4_pass"), writes=())
    r = result(rule, "L4")
    assert not r.ok and any("Task.state" in m and "writes" in m for m in r.messages)


@pytest.mark.parametrize(
    "change",
    [{"requires_decision": True, "evidence": False}, {"cls": "action"}, {"gated": True, "requires_decision": False}],
    ids=["evidence-mismatch", "action-without-decision", "gated-without-decision"],
)
def test_l8_variants(change):
    assert not result(dataclasses.replace(case("l8_pass"), **change), "L8").ok


def test_report_hash_is_stable_and_names_the_rule():
    from gnomon.footprint.lints import lint

    rule = case("l1_pass")
    a, b = lint(rule), lint(rule)
    assert a.sha == b.sha and len(a.sha) == 64
    assert a.rule_sha == rule.sha
    assert lint(case("l1_fail")).sha != a.sha


def test_l1_exempts_genesis_prelude():
    from gnomon.footprint.lints import lint
    from gnomon.kernel.registry import load_registry

    assert result(load_registry().get("Genesis"), "L1").ok
    assert lint(load_registry().get("Genesis")).ok
