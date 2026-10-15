"""M3 RegisterRule proposals are linted by the kernel service before submission (master §5.5, §9.13; D-18)."""

import dataclasses
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.gate_g2a]

CASES = Path(__file__).resolve().parents[2] / "fixtures" / "rules" / "lint_cases"


def m3(k, rule, pid):
    from gnomon.kernel.registry import register_rule_params

    return k.submit("RegisterRule", register_rule_params(rule), pid=pid, agent="ops-1")


def test_registration_runs_lints(genesis, snap):
    from gnomon.kernel.registry import load_rule_file

    before = snap(genesis.graph)
    out = m3(genesis, load_rule_file(CASES / "l1_fail.yaml"), "m3-l1")
    assert out.code == "SCHEMA_REJECTED"
    assert [r["id"] for r in out.detail["lints"] if not r["ok"]] == ["L1"]
    assert snap(genesis.graph) == before  # refused before submission: nothing ran


@pytest.mark.parametrize("lint_id", [f"L{n}" for n in range(1, 9)])
def test_every_lint_failure_is_refused(genesis, lint_id):
    from gnomon.kernel.registry import load_rule_file

    out = m3(genesis, load_rule_file(CASES / f"{lint_id.lower()}_fail.yaml"), f"m3-{lint_id}")
    assert out.code == "SCHEMA_REJECTED"
    assert lint_id in [r["id"] for r in out.detail["lints"] if not r["ok"]]


def test_clean_rule_registers_with_its_lint_sha(genesis):
    from gnomon.footprint.lints import lint
    from gnomon.kernel.registry import load_rule_file

    rule = load_rule_file(CASES / "l1_pass.yaml")
    out = m3(genesis, rule, "m3-ok")
    assert out.code == "APPLIED", out
    stored = genesis.read("MATCH (r:Rule {name: 'LintL1Pass'}) RETURN r.sha, r.lint_sha")
    assert stored == [[rule.sha, lint(rule).sha]]


def test_caller_supplied_lint_sha_is_replaced(genesis):
    from gnomon.footprint.lints import lint
    from gnomon.kernel.registry import load_rule_file, register_rule_params

    rule = load_rule_file(CASES / "l1_pass.yaml")
    p = register_rule_params(rule)
    p["def"]["lint_sha"] = "f" * 64
    assert genesis.submit("RegisterRule", p, pid="m3-forged", agent="ops-1").code == "APPLIED"
    assert genesis.read("MATCH (r:Rule {name: 'LintL1Pass'}) RETURN r.lint_sha") == [[lint(rule).sha]]


def test_unparseable_definition_is_refused(genesis):
    from gnomon.kernel.registry import load_rule_file

    broken = dataclasses.replace(load_rule_file(CASES / "l1_pass.yaml"), body="MATCH (t:Task {id: $p.task}")
    out = m3(genesis, broken, "m3-broken")
    assert out.code == "SCHEMA_REJECTED"
    assert "parse" in out.detail["reason"].lower()
