"""Parameter binding (master §6.8): write keys from the pinned templates and $p only; params_json = JCS($p)."""

from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.gate_g1b]

ROOT = Path(__file__).resolve().parents[3]
NOOP_YAML = ROOT / "tests" / "fixtures" / "rules" / "noop.yaml"
INF = 9007199254740991
ZERO64 = "0" * 64


@pytest.fixture(scope="module")
def noop():
    from gnomon.kernel.registry import load_rule_file

    return load_rule_file(NOOP_YAML)


def proposal(**kw):
    from gnomon.kernel.service import Proposal

    base = dict(rule="Noop", p={"note": "n"}, pid="pid-1", agent="agent-k", read_seq=1, view_keys=())
    base.update(kw)
    return Proposal(**base)


@pytest.mark.parametrize(
    ("templates", "p", "keys"),
    [
        (["Head"], {}, ["Head"]),
        (["Noop:*"], {}, ["Noop:*"]),
        (["Noop:{p.key}"], {"key": "k1"}, ["Noop:k1"]),
        (["Noop:{p.key}"], {}, []),                         # missing field: no key
        (["Noop:{p.key}"], {"key": None}, []),              # null field: no key
        (["Tag:{p.tags[]}"], {"tags": ["b", "a", None, "b"]}, ["Tag:b", "Tag:a"]),  # nulls skipped, deduped
        (["Item:{p.items[].id}:state"], {"items": [{"id": 2}, {"id": None}, {"x": 1}, {"id": 1}]},
         ["Item:2:state", "Item:1:state"]),
        (["Rw:{pid}"], {}, ["Rw:pid-1"]),
        (["A:{p.a}:{p.b}"], {"a": "x", "b": 3}, ["A:x:3"]),
        (["Noop:*", "Noop:{p.key}", "Noop:*"], {"key": "k"}, ["Noop:*", "Noop:k"]),
    ],
    ids=["literal", "phantom", "field", "missing", "null", "list", "list-field", "pid", "two-fields", "dedupe"],
)
def test_instantiate_keys(templates, p, keys):
    from gnomon.kernel.service import instantiate_keys

    assert instantiate_keys(templates, p, "pid-1") == keys


@pytest.mark.parametrize("bad", ["Noop:{q.key}", "Noop:{p.}", "Noop:{p.key", "Noop:{agent}", "Noop:{p.a[].b[]}"])
def test_bad_templates_are_refused(bad):
    from gnomon.kernel.service import instantiate_keys

    with pytest.raises(ValueError, match="key template"):
        instantiate_keys([bad], {"key": "k", "a": [{"b": ["x"]}]}, "pid-1")


def test_bind_uses_pinned_templates_and_jcs_params(noop):
    from gnomon.kernel.jcs import canonicalize
    from gnomon.kernel.service import bind

    p = {"tags": ["t2", "t1"], "note": "é", "key": "k9", "items": [{"id": "i1"}]}
    params = bind(noop, proposal(p=p, view_keys=("Noop:*", "Tag:t1")))
    assert params["write_keys"] == ["Noop:*", "Noop:k9", "Tag:t2", "Tag:t1", "Item:i1:state"]
    assert params["params_json"] == canonicalize(p)
    assert params["p"] == p
    assert params["rule"] == "Noop" and params["rule_sha"] == noop.sha
    assert params["pid"] == "pid-1" and params["agent"] == "agent-k"
    assert params["read_seq"] == 1 and params["view_keys"] == ["Noop:*", "Tag:t1"]
    assert params["mode"] == "live" and params["rec_pt"] is None and params["rec_lc"] is None
    assert params["attach"] is None
    assert params["INF"] == INF and params["ZERO64"] == ZERO64


def test_bind_ignores_caller_supplied_keys(noop):
    from gnomon.kernel.service import bind

    params = bind(noop, proposal(p={"note": "x", "write_keys": ["Evil:*"]}))
    assert params["write_keys"] == ["Noop:*"]


def test_bind_records_replay_clock(noop):
    from gnomon.kernel.service import bind

    params = bind(noop, proposal(mode="replay", rec_pt=1791000000000, rec_lc=3))
    assert (params["mode"], params["rec_pt"], params["rec_lc"]) == ("replay", 1791000000000, 3)


@pytest.mark.parametrize(
    "change",
    [{"p": ["not", "a", "map"]}, {"pid": ""}, {"read_seq": -1}, {"view_keys": ("ok", 7)}, {"mode": "later"},
     {"p": {"x": float("nan")}}, {"agent": ""}],
    ids=["p-not-map", "empty-pid", "negative-read-seq", "non-string-view-key", "bad-mode", "nan-param", "no-agent"],
)
def test_bind_rejects_malformed_proposals(noop, change):
    from gnomon.kernel.service import SchemaRejected, bind

    with pytest.raises(SchemaRejected):
        bind(noop, proposal(**change))
