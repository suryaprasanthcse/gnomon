"""§15.4 sim mode: a decision rule composes as P0 · BODY · Q0 · RET with no P1 and no E0, and needs no envelope."""

import dataclasses
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.gate_tb]

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def action():
    from gnomon.kernel.registry import load_rule_file

    rule = load_rule_file(ROOT / "tests" / "fixtures" / "rules" / "noop_action.yaml")
    assert rule.requires_decision
    return rule


def test_sim_plan_drops_p1_and_e0(action):
    from gnomon.kernel import composer

    assert composer.plan(action) == ["P0", "P1", "BODY", "Q0", "E0"]
    assert composer.plan(action, sim=True) == ["P0", "BODY", "Q0", "RET"]


def test_sim_composition_has_no_decision_segments(action):
    from gnomon.kernel import composer

    text = composer.compose(action, sim=True)
    assert "$d." not in text and "Scorer" not in text and "Decision" not in text
    assert text.count("Kernel.tick(") == 1 and text.rstrip().endswith("RETURN r.seq AS seq, r.hash AS hash")
    assert composer.compose(action) != text


def test_sim_binding_needs_no_attach_and_a_live_binding_still_does(action):
    from gnomon.kernel.service import Proposal, SchemaRejected, bind

    sim = Proposal(rule=action.name, p={"mark": "m"}, pid="sim:D-1:1:1", agent="executive", read_seq=5, mode="sim",
                   rec_pt=10, rec_lc=1)
    params = bind(action, sim)
    assert params["mode"] == "sim" and "d" not in params and params["rec_lc"] == 1
    with pytest.raises(SchemaRejected, match="attach"):
        bind(action, dataclasses.replace(sim, mode="live"))

