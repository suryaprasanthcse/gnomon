"""Macros (master §0.3): KCTX and LIVE(f) expand exactly, and expansion happens before hashing."""

import dataclasses

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.gate_g1b]


def expand(text):
    from gnomon.kernel.macros import expand

    return expand(text)


def test_kctx_expands_to_kernel_context():
    assert expand("WITH KCTX") == "WITH h, causes, nseq, now, now_lc"


def test_live_expands_with_its_argument():
    assert expand("WHERE LIVE(f)") == "WHERE (f.te = $INF AND f.vf <= now AND now < f.vt)"
    assert expand("WHERE LIVE(own) AND LIVE(dep2)") == (
        "WHERE (own.te = $INF AND own.vf <= now AND now < own.vt) AND "
        "(dep2.te = $INF AND dep2.vf <= now AND now < dep2.vt)"
    )


def test_both_macros_in_one_body():
    body = "MATCH (s)-[f:OWNED_BY]->(t)\nWITH KCTX, f WHERE LIVE(f)\nWITH KCTX"
    assert expand(body) == (
        "MATCH (s)-[f:OWNED_BY]->(t)\n"
        "WITH h, causes, nseq, now, now_lc, f WHERE (f.te = $INF AND f.vf <= now AND now < f.vt)\n"
        "WITH h, causes, nseq, now, now_lc"
    )


@pytest.mark.parametrize("text", ["WITH KCTXS", "WITH XKCTX", "n.KCTX", "OLIVE(f)", "LIVES(f)", "'kctx'"])
def test_only_whole_macro_tokens_expand(text):
    assert expand(text) == text


def test_expansion_is_idempotent():
    once = expand("WITH KCTX WHERE LIVE(f)")
    assert expand(once) == once


def test_rule_body_is_expanded_before_hashing(tmp_path):
    from gnomon.kernel.registry import load_rule_file

    (tmp_path / "m.cypher").write_text("WITH KCTX WHERE LIVE(f)\nWITH KCTX\n", encoding="utf-8")
    (tmp_path / "m.yaml").write_text(
        "name: MacroProbe\nversion: 1\nclass: kernel\nlayer: core\ncaller: [kernel]\nprelude: P0\n"
        "body_file: m.cypher\n",
        encoding="utf-8",
    )
    rule = load_rule_file(tmp_path / "m.yaml")
    assert "KCTX" not in rule.body and "LIVE(" not in rule.body
    assert rule.body == expand("WITH KCTX WHERE LIVE(f)\nWITH KCTX\n")
    assert '"body":' + '"' + rule.body.replace("\n", "\\n") + '"' in rule.def_json

    unexpanded = dataclasses.replace(rule, body="WITH KCTX WHERE LIVE(f)\nWITH KCTX\n")
    assert unexpanded.sha != rule.sha  # the expanded text is what is hashed
