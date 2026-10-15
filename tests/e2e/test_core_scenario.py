"""The Core scenario (master §11.4, §13): generator, infrastructure, dispatcher, scheduler and four LLM roles."""

import pytest

from gnomon.agents.recorded import PolicyLLM, RecordedLLM, RecordingLLM
from gnomon.audit.runner import audit
from gnomon.sim.scenario import run_core

pytestmark = [pytest.mark.e2e, pytest.mark.gate_g4b]

APPLIED_RULES = {"Observe.alert", "Observe.health", "OpenIncident", "ClaimTask", "ExpireLease", "ProposeCause",
                 "PostUpdate", "CompleteTask", "ResolveIncident", "Observe.receipt"}


def test_core_scenario_resolves_the_incident(core_run):
    r = core_run
    assert r.resolved and r.rounds < 60
    assert APPLIED_RULES <= {rule for rule in r.rules_applied}
    assert r.kernel.read("MATCH (i:Incident) RETURN i.state") == [["resolved"]]
    assert r.kernel.read("MATCH (t:Task) WHERE t.state <> 'done' RETURN count(t)") == [[0]]
    assert r.kernel.read("MATCH (:Incident)-[rc:ROOT_CAUSE]->(d:Deploy) WHERE rc.te = 9007199254740991 "
                         "RETURN d.id") == [[r.bad_deploy["id"]]]
    assert r.llm_calls > 0 and r.llm_misses == r.llm_calls  # the first run records every response


def test_core_scenario_triple(kernels):
    store: dict = {}
    first = run_core(kernels(), seed=7, llm=RecordingLLM(PolicyLLM(), store), crash=True)
    recorded = len(store)
    replays = [RecordedLLM(store), RecordedLLM(store)]
    second = run_core(kernels(), seed=7, llm=replays[0], crash=True)
    third = run_core(kernels(), seed=7, llm=replays[1], crash=True)
    assert first.resolved and second.resolved and third.resolved
    assert first.state_hash == second.state_hash == third.state_hash
    assert len(store) == recorded and replays[0].calls == replays[1].calls == first.llm_calls
    assert second.log == third.log == first.log
    other = run_core(kernels(), seed=8, llm=RecordingLLM(PolicyLLM(), {}), crash=True)
    assert other.state_hash != first.state_hash


def test_core_scenario_from_the_committed_recording(kernels, recorded_llm):
    r = run_core(kernels(), seed=7, llm=recorded_llm, crash=True)
    assert r.resolved and r.state_hash == recorded_llm.meta["state_hash"]


def test_core_scenario_without_a_crash(kernels):
    r = run_core(kernels(), seed=7, llm=RecordingLLM(PolicyLLM(), {}), crash=False)
    assert r.resolved and r.crash is None and "ExpireLease" not in r.rules_applied


def test_core_scenario_graph_passes_every_audit(core_run, db, redis_raw):
    report = audit(db, redis_raw, core_run.kernel.graph.name)
    assert report.ok, report.failed
