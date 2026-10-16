"""A17 · gate SHIP · the submission documents (master §29 rule 10, rule 11; RUNBOOK_A A17).

No database: these read the repository as a judge would. The README must carry every rule-10 item, the AI-assistance
section must equal what scripts/gen_disclosure.py renders from .agent/LEDGER.md, and the per-track pages and the video
script must exist and name the proofs they rest on.
"""

import importlib.util
import re
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.gate_ship]

ROOT = Path(__file__).resolve().parents[2]
README = (ROOT / "README.md").read_text(encoding="utf-8") if (ROOT / "README.md").exists() else ""
PROOFS = ["P1", "P2", "P3", "P4", "P5", "A", "B", "C", "D", "E", "F", "G"]
MCP_TOOLS = ["whoami", "read_entity", "facts", "as_of", "incident_status", "list_actions", "propose", "neighborhood",
             "rank_critical", "brokers", "failure_domains", "communities", "dependency_path", "why", "simulate",
             "decide", "decision", "optimizer_status", "send_notice", "fedlog"]
ALGORITHMS = ["algo.pageRank", "algo.betweenness", "algo.WCC", "algo.labelPropagation", "algo.SPpaths", "algo.bfs"]


def generator():
    path = ROOT / "scripts" / "gen_disclosure.py"
    assert path.exists(), "scripts/gen_disclosure.py renders the AI-assistance section from the ledger"
    spec = importlib.util.spec_from_file_location("gen_disclosure", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def has_word(text: str, word: str) -> bool:
    return re.search(rf"(?<![\w.]){re.escape(word)}(?![\w])", text) is not None


def test_readme_contains_rule10_items():
    lowered = README.lower()
    for heading in ("setup", "graph data model", "rule templates", "queries", "algorithms", "scoring", "demo video",
                    "tracks entered", "ai assistance"):
        assert re.search(rf"^#{{1,3}} .*{heading}", lowered, re.M), f"README has no section on {heading!r}"
    assert "docker compose up" in README and "uv run" in README and "VIDEO-LINK-PENDING" in README
    # the graph model of §3: every kernel label, domain label and fact type
    for name in ("Head", "Rule", "KeyVersion", "Rewrite", "FactType", "Outbox", "Scorer", "Approval", "Decision",
                 "Candidate", "Evidence", "IndexEntry", "Channel", "XMsg", "Agent", "Service", "Incident", "Task",
                 "OWNED_BY", "DEPENDS_ON", "DEPLOYED", "REPLICA_OF", "MEMBER_OF", "ROOT_CAUSE", "CONSUMES", "DEP_NOW",
                 "OWNER_NOW", "CAUSED_BY", "COMMITTED_AS", "CONSIDERED", "JUSTIFIED_BY", "APPROVES"):
        assert has_word(README, name), f"graph model omits {name}"
    # the as-of queries, the algorithms, the scorer and the cost model
    for q in ("Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"):
        assert has_word(README, q), f"README omits as-of query {q}"
    for algo in ALGORITHMS:
        assert algo in README, f"README omits {algo}"
    for term in ("score_num", "benefit", "harm", "net_us", "known_at_seq", "valid_at"):
        assert term in README, f"README omits {term}"


def test_readme_names_every_rule_template_path():
    templates = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "gnomon" / "rules").rglob("*")
                       if p.suffix in (".cypher", ".tmpl"))
    assert len(templates) >= 40
    missing = [t for t in templates if t not in README]
    assert not missing, f"rule templates missing from the README: {missing}"
    assert "gnomon/rules/registry.yaml" in README


def test_readme_covers_proofs_audits_mutations_and_the_mcp_surface():
    for proof in PROOFS:
        assert re.search(rf"\bProof {proof}\b|\| {proof} \|", README), f"README omits proof {proof}"
    for k in range(1, 10):
        assert has_word(README, f"K{k}")
    assert has_word(README, "MU1") and has_word(README, "MU32")
    for tool in MCP_TOOLS:
        assert f"`{tool}`" in README, f"README omits MCP tool {tool}"
    for command in ("scripts/demo_proofs.sh", "scripts/demo_proofs.ps1", "docs/video_script.md", "docs/design/master.md"):
        assert command in README


def test_the_ai_disclosure_is_generated_from_the_ledger():
    gen = generator()
    ledger = (ROOT / ".agent" / "LEDGER.md").read_text(encoding="utf-8")
    block = gen.extract(README)
    assert block is not None, "README has no generated AI-assistance block"
    assert block == gen.render(ledger)
    for model in set(re.findall(r"\| (claude-[\w.-]+) \|", ledger)):
        assert model in block
    rows = gen.parse(ledger)
    assert {r["module"] for r in rows} >= {f"A{n:02d}" for n in range(0, 17)}


def test_the_disclosure_generator_is_deterministic_and_ignores_the_ship_rows():
    gen = generator()
    ledger = ("| utc | module | gate | result | model | session | notes |\n|---|---|---|---|---|---|---|\n"
              "| 2026-10-15T05:46:43Z | A00 | H0 | green | claude-opus-5-5 | manual | x |\n"
              "| 2026-10-16T08:00:00Z | A09 | G4b | green | claude-sonnet-5-5 | manual | y |\n"
              "| 2026-10-17T08:00:00Z | A17 | SHIP | green | claude-sonnet-5-5 | manual | z |\n")
    first = gen.render(ledger)
    assert first == gen.render(ledger)
    assert "claude-opus-5-5" in first and "A00" in first and "A09" in first
    assert gen.render(ledger + "| 2026-10-18T00:00:00Z | A17 | SHIP | green | claude-sonnet-5-5 | manual | w |\n") == first
    spliced = gen.splice("intro\n<!-- AI-DISCLOSURE:BEGIN -->\nold\n<!-- AI-DISCLOSURE:END -->\noutro\n", ledger)
    assert gen.extract(spliced) == first and spliced.startswith("intro\n") and spliced.endswith("outro\n")


@pytest.mark.parametrize("track", ["01", "02", "03"])
def test_each_track_page_maps_focus_areas_to_proofs(track):
    page = ROOT / "docs" / "tracks" / f"{track}.md"
    assert page.exists(), f"docs/tracks/{track}.md"
    text = page.read_text(encoding="utf-8")
    assert f"docs/tracks/{track}.md" in README
    assert len(re.findall(r"\bProof [A-GP]\w?\b", text)) >= 2 and "master.md" in text
    assert re.search(r"^\|.*\|.*\|", text, re.M), "the page maps focus areas in a table"


def test_the_video_script_walks_every_proof():
    script = (ROOT / "docs" / "video_script.md").read_text(encoding="utf-8")
    for proof in PROOFS:
        assert re.search(rf"\bProof {proof}\b", script), f"video script omits proof {proof}"
    assert "scripts/demo_proofs" in script


def test_the_only_contributor_in_history_is_the_human_author():
    out = subprocess.run(["git", "log", "--format=%an"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0 and set(out.stdout.split("\n")) - {""} == {"Surya Prasanth R"}
    assert "Surya Prasanth R" in README
