"""Outcomes and diagnosis (master §6.9, §6.10, Appendix A): each rejection is named, and nothing is written."""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.gate_g1b]


def test_duplicate(genesis):
    first = genesis.submit("Noop", {"note": "same"}, pid="pid-d")
    again = genesis.submit("Noop", {"note": "same"}, pid="pid-d")
    assert first.code == "APPLIED"
    assert (again.code, again.seq, again.hash) == ("DUPLICATE", first.seq, first.hash)
    assert genesis.head()["seq"] == 2


def test_pid_conflict(genesis):
    genesis.submit("Noop", {"note": "one"}, pid="pid-c")
    outcome = genesis.submit("Noop", {"note": "two"}, pid="pid-c")
    assert outcome.code == "PID_CONFLICT"
    assert outcome.detail["seq"] == 2


def test_rule_pinned(genesis):
    genesis.graph.query("MATCH (r:Rule {name: 'Noop'}) SET r.active = false")
    outcome = genesis.submit("Noop", {"note": "x"}, pid="pid-p")
    assert outcome.code == "RULE_PINNED"
    assert outcome.detail == {"rule": "Noop", "rule_sha": genesis.registry.get("Noop").sha}


@pytest.mark.parametrize("agent", ["triage-1", "ghost"], ids=["role-not-caller", "unknown-agent"])
def test_caller_denied(genesis, agent):
    outcome = genesis.submit("Noop", {"note": "x"}, pid="pid-a", agent=agent)
    assert outcome.code == "CALLER_DENIED"
    assert outcome.detail["agent"] == agent


def test_inactive_agent_denied(genesis):
    genesis.graph.query("MATCH (a:Agent {id: 'kernel'}) SET a.active = false")
    assert genesis.submit("Noop", {"note": "x"}, pid="pid-i").code == "CALLER_DENIED"


def test_invalid_view(genesis):
    outcome = genesis.submit("Noop", {"note": "x"}, pid="pid-v", read_seq=6)
    assert outcome.code == "INVALID_VIEW"
    assert outcome.detail == {"read_seq": 6, "head_seq": 1}


def test_rejected_stale_names_key_seq_and_writer(genesis):
    genesis.submit("Noop", {"note": "w", "key": "k1"}, pid="pid-w", agent="kernel")
    outcome = genesis.submit("Noop", {"note": "late"}, pid="pid-late", read_seq=1, view_keys=["Head", "Noop:k1"])
    assert outcome.code == "REJECTED_STALE"
    assert outcome.detail["stale"] == [
        {"key": "Noop:k1", "changed_at": 2, "by_rewrite": "pid-w", "by_agent": "kernel", "by_rule": "Noop"},
    ]
    fresh = genesis.submit("Noop", {"note": "late"}, pid="pid-late", read_seq=2, view_keys=["Head", "Noop:k1"])
    assert fresh.code == "APPLIED"


def test_rejected_guard_names_the_probe(genesis):
    genesis.graph.query("CREATE (:Blocker)")
    outcome = genesis.submit("Noop", {"note": "x"}, pid="pid-g")
    assert outcome.code == "REJECTED_GUARD"
    assert outcome.detail == {"guard": "no_blocker"}


def test_indeterminate_when_every_probe_passes(genesis):
    from gnomon.kernel.service import Proposal

    proposal = Proposal(rule="Noop", p={"note": "x"}, pid="pid-n", agent="kernel", read_seq=1, view_keys=("Head",))
    outcome = genesis.service.diagnose(proposal)
    assert outcome.code == "INDETERMINATE"


def test_diagnosis_order(genesis):
    """D1 before D2 before D3 before D4 before D5 before D8."""
    genesis.submit("Noop", {"note": "w", "key": "k"}, pid="pid-1")
    genesis.graph.query("CREATE (:Blocker)")
    # stale view + blocker: D5 wins over D8
    assert genesis.submit("Noop", {"note": "s"}, pid="pid-2", read_seq=1, view_keys=["Noop:k"]).code == \
        "REJECTED_STALE"
    # invalid view + stale-looking keys: D4 wins over D5
    assert genesis.submit("Noop", {"note": "s"}, pid="pid-3", read_seq=9, view_keys=["Noop:k"]).code == "INVALID_VIEW"
    # existing pid with different content + invalid view: D3 wins over D4
    assert genesis.submit("Noop", {"note": "other"}, pid="pid-1", read_seq=9).code == "PID_CONFLICT"
    # unknown caller + existing pid: D2 wins over D3
    assert genesis.submit("Noop", {"note": "w", "key": "k"}, pid="pid-1", agent="ghost").code == "CALLER_DENIED"
    # deactivated rule + unknown caller: D1 wins over D2
    genesis.graph.query("MATCH (r:Rule {name: 'Noop'}) SET r.active = false")
    assert genesis.submit("Noop", {"note": "x"}, pid="pid-4", agent="ghost").code == "RULE_PINNED"


@pytest.mark.parametrize(
    ("rule", "p"),
    [("NoSuchRule", {"note": "x"}), ("Noop", ["not", "a", "map"])],
    ids=["unknown-rule", "params-not-a-map"],
)
def test_schema_rejected(genesis, rule, p):
    outcome = genesis.submit(rule, p, pid="pid-s")
    assert outcome.code == "SCHEMA_REJECTED"
    assert outcome.detail["reason"]


def test_zero_rows_writes_nothing(genesis, snap):
    genesis.submit("Noop", {"note": "w", "key": "k"}, pid="pid-w")
    genesis.graph.query("CREATE (:Blocker)")
    before = snap(genesis.graph)
    rejections = [
        dict(rule="Noop", p={"note": "w", "key": "k"}, pid="pid-w"),                       # DUPLICATE
        dict(rule="Noop", p={"note": "z"}, pid="pid-w"),                                   # PID_CONFLICT
        dict(rule="Noop", p={"note": "z"}, pid="pid-x", agent="ghost"),                    # CALLER_DENIED
        dict(rule="Noop", p={"note": "z"}, pid="pid-x", read_seq=99),                      # INVALID_VIEW
        dict(rule="Noop", p={"note": "z"}, pid="pid-x", read_seq=1, view_keys=["Noop:k"]),  # REJECTED_STALE
        dict(rule="Noop", p={"note": "z"}, pid="pid-x"),                                   # REJECTED_GUARD
        dict(rule="Noop", p={"note": "z", "boom": True}, pid="pid-y"),                     # REJECTED_GUARD first
        dict(rule="Genesis", p={}, pid="pid-g"),                                           # REJECTED_GUARD (PG)
    ]
    codes = []
    for kw in rejections:
        rule, p = kw.pop("rule"), kw.pop("p")
        codes.append(genesis.submit(rule, p, **kw).code)
        assert snap(genesis.graph) == before, codes[-1]
    assert codes == ["DUPLICATE", "PID_CONFLICT", "CALLER_DENIED", "INVALID_VIEW", "REJECTED_STALE",
                     "REJECTED_GUARD", "REJECTED_GUARD", "REJECTED_GUARD"]
    genesis.graph.query("MATCH (b:Blocker) DELETE b")
    genesis.graph.query("MATCH (r:Rule {name: 'Noop'}) SET r.active = false")
    before = snap(genesis.graph)
    assert genesis.submit("Noop", {"note": "z"}, pid="pid-z").code == "RULE_PINNED"
    assert snap(genesis.graph) == before
