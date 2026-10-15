"""The shipped .cypher files obey the platform hazards (FACTS S23, S24, S25, D-30) and never write (§10.1 rule 5)."""

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.gate_g4a]

ROOT = Path(__file__).resolve().parents[3] / "gnomon" / "query"
FILES = {
    "enablement": ["en_r4", "en_r5", "en_r6", "en_r7", "en_r9", "en_r10", "en_r11"],
    "reads": ["rd_facts", "rd_entity", "q1", "q2", "q3", "q4", "q5", "q6", "q7"],
    "algorithms": ["al_neighborhood", "al_neighborhood_up", "al_rank_critical", "al_brokers", "al_domains",
                   "al_communities", "al_path"],
}
ALL = [(d, n) for d, names in FILES.items() for n in names]
WRITE_CLAUSE = re.compile(r"\b(CREATE|MERGE|SET|DELETE|DETACH|REMOVE|FOREACH|DROP)\b", re.I)
# D-30: an inline property map whose value is a property of an earlier variable, e.g. {id: e.rw}
INLINE_REF = re.compile(r"\{[^{}]*:\s*(?<![$\w.])[A-Za-z_]\w*\.\w+[^{}]*\}")


def strip_comments(text):
    return "\n".join(line.split("//")[0] for line in text.splitlines())


def read(d, n):
    return (ROOT / d / f"{n}.cypher").read_text(encoding="utf-8")


@pytest.mark.parametrize(("d", "n"), ALL, ids=[f"{d}/{n}" for d, n in ALL])
def test_query_file_is_read_only_and_platform_safe(d, n):
    text = read(d, n)
    body = strip_comments(text)
    assert body.strip(), "empty query"
    assert text.isascii(), "S24: wire text must be ASCII"
    assert not WRITE_CLAUSE.search(body), "a query file writes"
    assert not INLINE_REF.search(body), "D-30: inline map references an earlier variable; use WHERE"
    assert not re.search(r"OPTIONAL MATCH[^\n]*\n\s*WHERE", body), "S25: no WHERE on OPTIONAL MATCH"


@pytest.mark.parametrize(("d", "n"), [(d, n) for d, n in ALL if d != "algorithms"],
                         ids=[f"{d}/{n}" for d, n in ALL if d != "algorithms"])
def test_binding_reads_return_read_seq_from_head(d, n):
    body = strip_comments(read(d, n))
    assert "MATCH (h:Head)" in body and "AS read_seq" in body


@pytest.mark.parametrize(("d", "n"), ALL, ids=[f"{d}/{n}" for d, n in ALL])
def test_s23_no_property_projection_after_an_aggregate_in_one_clause(d, n):
    body = strip_comments(read(d, n))
    stop = r"\b(?:WHERE|ORDER BY|LIMIT|SKIP|UNION|MATCH|OPTIONAL MATCH|WITH|RETURN|CALL|UNWIND|YIELD)\b"
    for match in re.finditer(r"\b(?:WITH|RETURN)\b(.*?)(?=" + stop + r"|\Z)", body, re.S):
        items = [i.strip() for i in re.split(r",(?![^()\[\]{}]*[)\]}])", match.group(1)) if i.strip()]
        agg = [k for k, i in enumerate(items) if re.search(r"\b(count|sum|collect|min|max|avg)\s*\(", i)]
        if agg:
            later = [i for i in items[agg[0] + 1:] if i not in [items[k] for k in agg]]  # another aggregate is fine
            assert not [i for i in later if re.search(r"[A-Za-z_]\w*\.\w", i)], (match.group(0), later)
