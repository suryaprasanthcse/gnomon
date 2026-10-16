"""Segment composition (master §6.1): segment order by rule kind, T0 substitution; P1/E0 installed by A10, P2/P3 by A13."""

import dataclasses
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.gate_g1b]

ROOT = Path(__file__).resolve().parents[3]
NOOP_YAML = ROOT / "tests" / "fixtures" / "rules" / "noop.yaml"
T0 = "Kernel.tick(h.pt, h.lc, timestamp(), $mode, $rec_pt, $rec_lc)"


@pytest.fixture(scope="module")
def genesis():
    from gnomon.kernel.registry import load_registry

    return load_registry().get("Genesis")


@pytest.fixture(scope="module")
def noop():
    from gnomon.kernel.registry import load_rule_file

    return load_rule_file(NOOP_YAML)


def _in_order(text: str, parts: list[str]) -> bool:
    at = 0
    for part in parts:
        found = text.find(part, at)
        if found < 0:
            return False
        at = found + len(part)
    return True


def test_genesis_is_pg_body_q0_ret(genesis):
    from gnomon.kernel import composer

    assert composer.plan(genesis) == ["PG", "BODY", "Q0", "RET"]
    text = composer.compose(genesis)
    assert _in_order(text, [composer.segment("PG"), genesis.body, composer.segment("Q0"), composer.segment("RET")])
    assert "MATCH (h:Head)\n" not in text.split(genesis.body)[0]  # no P0 before the body


def test_standard_is_p0_body_q0_ret_with_t0(noop):
    from gnomon.kernel import composer

    assert composer.plan(noop) == ["P0", "BODY", "Q0", "RET"]
    text = composer.compose(noop)
    p0 = composer.segment("P0")
    assert "<TICK>" in p0
    assert "<TICK>" not in text
    assert _in_order(text, [p0.split("<TICK>")[0], T0, p0.split("<TICK>")[1], noop.body, composer.segment("Q0"),
                            composer.segment("RET")])
    assert text.count("Kernel.tick(") == 1


def test_every_composed_query_ticks_exactly_once(genesis, noop):
    from gnomon.kernel import composer

    assert composer.compose(genesis).count("Kernel.tick(") == 1
    assert composer.compose(noop).count("Kernel.tick(") == 1


@pytest.mark.parametrize(
    ("change", "segment", "tick"),
    [({"prelude": "P2"}, "P2", "Kernel.recv(h.pt, h.lc, timestamp(), $m.s_pt, $m.s_lc, $mode, $rec_pt, $rec_lc)"),
     ({"prelude": "P3"}, "P3", "Kernel.recv(h.pt, h.lc, timestamp(), $m.r_pt, $m.r_lc, $mode, $rec_pt, $rec_lc)")],
    ids=["federation-receive", "federation-ack"],
)
def test_federation_segments_are_installed_and_replace_the_tick(noop, change, segment, tick):
    from gnomon.kernel import composer

    rule = dataclasses.replace(noop, **change)
    assert composer.plan(rule) == ["P0", segment, "BODY", "Q0", "RET"]
    text = composer.compose(rule)
    assert composer.segment(segment) in text and tick in text and "Kernel.tick(" not in text and "<TICK>" not in text


def test_decision_is_p0_p1_body_q0_e0(noop):
    from gnomon.kernel import composer

    rule = dataclasses.replace(noop, requires_decision=True, evidence=True, cls="action")
    assert composer.plan(rule) == ["P0", "P1", "BODY", "Q0", "E0"]
    text = composer.compose(rule)
    p0 = composer.segment("P0")
    head, tail = p0.split(composer.ADD_ON_MARK)
    assert _in_order(text, [head.split("<TICK>")[0], T0, head.split("<TICK>")[1], composer.segment("P1"), tail, rule.body,
                            composer.segment("Q0"), composer.segment("E0")])
    assert text.count("Kernel.tick(") == 1 and "<TICK>" not in text
    assert not text.rstrip().endswith("RETURN r.seq AS seq, r.hash AS hash")  # E0 returns the decision as well


def test_p1_runs_between_the_tick_and_the_apply_variables(noop):
    from gnomon.kernel import composer

    p0 = composer.segment("P0")
    head, tail = p0.split(composer.ADD_ON_MARK)
    assert "rule" in head.split("<TICK> AS c")[0].splitlines()[-1]  # `rule` stays in scope for P1 (rule.gated)
    assert "nseq" in tail and "nseq" not in head


def test_e0_contains_no_where():
    import re

    from gnomon.kernel import composer

    code = "\n".join(line.split("//")[0] for line in composer.segment("E0").splitlines())
    assert not re.search(r"\bWHERE\b", code)  # a filter after the writes could silently drop the evidence rows


def test_p1_checks_the_decision_before_anything_is_written():
    import re

    from gnomon.kernel import composer

    code = "\n".join(line.split("//")[0] for line in composer.segment("P1").splitlines())
    assert not re.search(r"\b(CREATE|MERGE|SET|DELETE|REMOVE)\b", code)
    for needle in ("$d.scorer_sha", "$d.read_seq = $read_seq", "Kernel.sha256($attach) = $p.attach_sha",
                   "$p.decision_sha = $d.sha", "rule.gated", "$d.cites"):
        assert needle in code, needle


def test_unknown_prelude_is_refused(noop):
    from gnomon.kernel import composer

    with pytest.raises(ValueError, match="prelude"):
        composer.compose(dataclasses.replace(noop, prelude="P9"))


def test_pg_only_for_genesis(noop):
    from gnomon.kernel import composer

    with pytest.raises(ValueError, match="Genesis"):
        composer.compose(dataclasses.replace(noop, prelude="PG"))


def test_kernel_source_pins_udfs_and_segments():
    import hashlib

    from gnomon.kernel import composer
    from gnomon.udf import loader

    src = composer.kernel_source()
    assert loader.source() in src
    for name in ("PG", "P0", "P1", "Q0", "E0", "RET"):
        assert composer.segment(name) in src
    assert (ROOT / "gnomon" / "kernel" / "composer.py").read_text(encoding="utf-8") in src
    assert composer.kernel_sha() == hashlib.sha256(src.encode("utf-8")).hexdigest()
    assert composer.kernel_source() == src  # deterministic
