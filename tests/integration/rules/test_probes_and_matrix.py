"""Generated guard probes (master §9 preamble) and the conflict matrix among R1–R11 (§7.6)."""

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.gate_g2b]

PROBES = {
    "Observe.alert": ["new_event", "service_exists"],
    "Observe.health": ["new_event", "service_exists", "valid_health", "newer_observation"],
    "Observe.receipt": ["new_event", "valid_outcome", "outbox_exists", "outbox_pending"],
    "OpenIncident": ["alert_on_service", "service_matches", "signature_matches", "has_tasks", "untriggered",
                     "new_incident_id", "new_task_ids", "no_open_duplicate", "skills_exist"],
    "ClaimTask": ["skill_match", "self_claim", "task_open", "ttl_positive", "ttl_max", "no_live_lease"],
    "CompleteTask": ["holds_task", "lease_open", "lease_live", "task_claimed"],
    "Handoff": ["holds_task", "lease_open", "lease_live", "task_claimed", "target_skilled", "target_active",
                "target_other", "ttl_positive", "ttl_max"],
    "ExpireLease": ["lease_on_task", "lease_open", "lease_expired", "task_claimed"],
    "PostUpdate": ["incident_exists", "incident_open", "has_cites", "cites_live"],
    "ProposeCause": ["incident_and_cause_exist", "incident_open", "cause_typed", "confidence_min", "confidence_max",
                     "not_already_current", "cites_live"],
    "ResolveIncident": ["incident_affects", "incident_open", "service_ok", "service_normal", "all_tasks_done",
                        "root_cause_present"],
}
CALLERS = {
    "Observe.alert": ("ingest",), "Observe.health": ("ingest",), "Observe.receipt": ("dispatcher",),
    "OpenIncident": ("triage",), "ClaimTask": ("comms", "investigator", "responder", "triage"),
    "CompleteTask": ("comms", "investigator", "responder", "triage"),
    "Handoff": ("comms", "investigator", "responder", "triage"), "ExpireLease": ("scheduler",),
    "PostUpdate": ("comms",), "ProposeCause": ("investigator",), "ResolveIncident": ("human", "investigator"),
}


@pytest.fixture(scope="module")
def registry():
    from gnomon.kernel.registry import load_registry

    return load_registry()


@pytest.mark.parametrize("name", sorted(PROBES))
def test_generated_probe_names(registry, name):
    rule = registry.get(name)
    assert [g["name"] for g in rule.guard_probes] == [f"G{k}:{label}" for k, label in enumerate(PROBES[name], 1)]


@pytest.mark.parametrize("name", sorted(PROBES))
def test_probes_are_read_only_prefixes_of_the_guard_section(registry, name):
    from gnomon.footprint.parser import extract

    rule = registry.get(name)
    for g in rule.guard_probes:
        text = g["cypher"]
        assert text.startswith("MATCH (h:Head) WITH h, [] AS causes, h.seq + 1 AS nseq, "), g["name"]
        assert text.rstrip().endswith("RETURN 1 AS ok LIMIT 1"), g["name"]
        body = text[text.index("\n") + 1:text.rindex("RETURN")]
        assert extract(body).writes == set(), g["name"]


def test_probe_generation_counts_conjuncts_and_plain_matches():
    from gnomon.kernel.registry import generate_probes

    body = ("OPTIONAL MATCH (e:Event {source_id: $p.s})\nWITH h, causes, nseq, now, now_lc, e\nWHERE e IS NULL\n"
            "MATCH (s:Service {id: $p.svc})\nWHERE s.health = 'ok' AND (s.mode = 'normal' OR s.mode = 'x')\n"
            "OPTIONAL MATCH (s)<-[:ON]-(a:Alert)\nWHERE a.severity = 'high' AND a.id <> ''\n"
            "WITH h, causes, nseq, now, now_lc, s, count(a) AS n\nWHERE n = 0\nSET s.seen = true\nWITH h, causes, nseq, "
            "now, now_lc\n")
    probes = generate_probes(body, ["a", "b", "c", "d", "e"])
    assert [p["name"] for p in probes] == ["G1:a", "G2:b", "G3:c", "G4:d", "G5:e"]
    assert "s.mode = 'normal' OR s.mode = 'x'" in probes[3]["cypher"]
    assert "a.severity" in probes[4]["cypher"]  # the OPTIONAL MATCH filter is kept, not a probe of its own
    assert "SET" not in probes[4]["cypher"]
    with pytest.raises(ValueError, match="5 guard points"):
        generate_probes(body, ["a"])


@pytest.mark.parametrize("name", sorted(CALLERS))
def test_callers_and_classes(registry, name):
    rule = registry.get(name)
    assert tuple(sorted(rule.caller)) == CALLERS[name]
    assert rule.cls == "memory" and rule.prelude == "P0" and rule.layer == "core"


def test_every_new_rule_passes_the_lints(registry):
    from gnomon.footprint.lints import lint

    for name in PROBES:
        report = lint(registry.get(name))
        assert report.ok, (name, [m for r in report.results for m in r.messages])


@pytest.fixture(scope="module")
def entries(registry):
    from gnomon.footprint.matrix import build

    return {(e.a, e.b): e for e in build(registry.rules(), registry.fact_types())}


def entry(entries, a, b):
    return entries.get((a, b)) or entries[(b, a)]


@pytest.mark.parametrize(
    ("a", "b", "element"),
    [
        ("ClaimTask", "ClaimTask", "HOLDS"), ("ClaimTask", "ClaimTask", "Task.state"),
        ("ClaimTask", "ExpireLease", "HOLDS"), ("ClaimTask", "ExpireLease", "Task.state"),
        ("CompleteTask", "Handoff", "HOLDS"),
        ("AssertFact.OWNED_BY", "PostUpdate", "OWNED_BY"),
        ("RetractFact.OWNED_BY", "ProposeCause", "OWNED_BY.te"),
        ("RetractFact.DEPENDS_ON", "ProposeCause", "DEPENDS_ON.te"),
        ("OpenIncident", "OpenIncident", "Incident"),
        ("ResolveIncident", "ClaimTask", "Task.state"), ("ResolveIncident", "CompleteTask", "Task.state"),
        ("Observe.receipt", "PostUpdate", "Outbox"),
    ],
)
def test_section_7_6_pairs_are_guarded(entries, a, b, element):
    e = entry(entries, a, b)
    assert e.cls == "guarded", e
    assert element in e.overlap


@pytest.mark.parametrize(("a", "b"), [("Observe.alert", "ClaimTask"), ("ProposeCause", "ClaimTask")])
def test_section_7_6_pairs_commute(entries, a, b):
    assert entry(entries, a, b).cls == "commutes"


def test_no_registered_pair_is_unguarded(entries):
    assert [e for e in entries.values() if e.cls == "unguarded"] == []
