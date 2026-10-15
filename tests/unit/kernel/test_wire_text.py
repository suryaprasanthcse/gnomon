"""The query text the kernel sends is plain ASCII (FACTS S24, DECISIONS D-20).

FalkorDB aborts when it truncates a slow write's text for its slow log inside a multi-byte character, so
the service strips comments (the only non-ASCII in segments and bodies) before sending a composed query.
"""

from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.gate_g2a]

ROOT = Path(__file__).resolve().parents[3]


def wire(text):
    from gnomon.kernel.service import wire_text

    return wire_text(text)


def test_every_composed_query_is_ascii_without_comments():
    from gnomon.kernel import composer
    from gnomon.kernel.registry import load_registry, load_rule_file

    rules = [*load_registry().rules(), load_rule_file(ROOT / "tests" / "fixtures" / "rules" / "noop.yaml")]
    for rule in rules:
        composed = composer.compose(rule)
        assert not composed.isascii(), rule.name  # the sources do carry non-ASCII comments
        text = wire(composed)
        assert text.isascii(), rule.name
        assert "//" not in text, rule.name


def test_comments_go_strings_stay():
    text = "// head ─ comment\nMATCH (t:T {a: 'x//y', b: \"q // r\"}) // trailing ─\n/* block\n ─ */RETURN 1\n"
    assert wire(text) == "MATCH (t:T {a: 'x//y', b: \"q // r\"})\nRETURN 1\n"


def test_escaped_quotes_inside_strings():
    text = "RETURN 'it\\'s // not a comment' AS s // but this is\n"
    assert wire(text) == "RETURN 'it\\'s // not a comment' AS s\n"


def test_backticked_names_stay():
    assert wire("MATCH (n:`odd//label`) RETURN n // x\n") == "MATCH (n:`odd//label`) RETURN n\n"


def test_stripping_keeps_the_footprint():
    from gnomon.footprint.parser import extract
    from gnomon.kernel.registry import load_registry

    for rule in load_registry().rules():
        before, after = extract(rule.body), extract(wire(rule.body))
        assert (before.reads, before.writes, before.guard_reads) == (after.reads, after.writes, after.guard_reads)
