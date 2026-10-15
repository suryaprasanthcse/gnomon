"""§13.2 LLM agent loop: the outcome table, driven by a scripted model and a scripted query service."""

import json
from types import SimpleNamespace

import pytest

from gnomon.agents.llm import AGENT_STALE_RETRIES, AgentBug, AgentLoop, Role
from gnomon.agents.recorded import MissingRecording, RecordedLLM, RecordingLLM, prompt_hash
from gnomon.kernel.outcomes import Code, Outcome

pytestmark = [pytest.mark.integration, pytest.mark.gate_g4b]

ROLE = Role(name="investigator", rules=("ClaimTask",), instruction="claim an open task")
CHOICE = json.dumps({"rule": "ClaimTask", "tuple_index": 0})


class ScriptedLLM:
    def __init__(self, *answers):
        self.answers, self.prompts = list(answers), []

    def complete(self, prompt):
        self.prompts.append(prompt)
        return self.answers[min(len(self.prompts), len(self.answers)) - 1]


class FakeQS:
    """Answers list_actions with one ClaimTask tuple and proposes with a scripted sequence of outcomes."""

    def __init__(self, *outcomes):
        self.outcomes, self.lists, self.proposals = list(outcomes), 0, []

    def session(self, agent):
        return SimpleNamespace(agent=agent)

    def list_actions(self, sess, rule, args=None):
        self.lists += 1
        return SimpleNamespace(read_id=f"rd-{self.lists}", tuples=[{"index": 0, "task": "t1", "incident": "i1"}])

    def read_entity(self, sess, uid):
        return SimpleNamespace(read_id="rd-e", rows=[{"labels": ["Task"], "props": {"incident": "i1"}}])

    def propose(self, sess, rule, **kw):
        self.proposals.append((rule, kw))
        return self.outcomes[min(len(self.proposals), len(self.outcomes)) - 1]


def applied():
    return Outcome(Code.APPLIED, seq=9)


def stale():
    return Outcome(Code.REJECTED_STALE, detail={"stale": [{"key": "Task:t1:state"}]})


def loop(qs, llm=None):
    return AgentLoop("inv-a", ROLE, qs, llm or ScriptedLLM(CHOICE))


def test_applied_continues():
    qs = FakeQS(applied())
    res = loop(qs).step()
    assert (res.state, res.attempts, res.rule) == ("applied", 1, "ClaimTask")
    rule, kw = qs.proposals[0]
    assert kw["tuple_index"] == 0 and kw["cite_reads"] == ["rd-1"] and kw["pid"] == res.pid


def test_stale_rereads_and_retries_then_applies():
    qs = FakeQS(stale(), stale(), applied())
    llm = ScriptedLLM(CHOICE)
    res = loop(qs, llm).step()
    assert (res.state, res.attempts) == ("applied", 3)
    assert qs.lists == 3 and len(llm.prompts) == 3  # each retry perceives and chooses again
    assert {kw["pid"] for _, kw in qs.proposals} == {res.pid}  # a rejected proposal wrote nothing, so the pid is reused


def test_stale_retries_are_bounded():
    qs = FakeQS(stale())
    res = loop(qs).step()
    assert AGENT_STALE_RETRIES == 3
    assert (res.state, res.attempts) == ("gave_up_stale", 1 + AGENT_STALE_RETRIES)
    assert len(qs.proposals) == 1 + AGENT_STALE_RETRIES


def test_guard_rejection_goes_back_to_enable():
    qs = FakeQS(Outcome(Code.REJECTED_GUARD, detail={"guard": "G3:task_open"}))
    res = loop(qs).step()
    assert (res.state, res.attempts) == ("reenable", 1) and len(qs.proposals) == 1
    assert loop(FakeQS(applied())).step().state == "applied"  # the next step lists the rules again


def test_duplicate_is_treated_as_applied():
    assert loop(FakeQS(Outcome(Code.DUPLICATE, seq=4))).step().state == "applied"


def test_caller_denied_is_a_bug():
    with pytest.raises(AgentBug):
        loop(FakeQS(Outcome(Code.CALLER_DENIED, detail={"agent": "inv-a"}))).step()


def test_kernel_error_is_surfaced():
    agent = loop(FakeQS(Outcome(Code.KERNEL_ERROR, detail={"error": "boom"})))
    res = agent.step()
    assert res.state == "surfaced" and agent.surfaced == [{"pid": res.pid, "rule": "ClaimTask", "error": "boom"}]


def test_idle_when_the_model_chooses_nothing():
    qs = FakeQS(applied())
    res = loop(qs, ScriptedLLM(json.dumps({"rule": None}))).step()
    assert res.state == "idle" and qs.proposals == []


def test_pids_are_unique_per_decision():
    qs = FakeQS(applied())
    a = loop(qs)
    first, second = a.step(), a.step()
    assert first.pid != second.pid and first.pid.startswith("inv-a:")


def test_prompt_is_canonical_json_of_what_the_agent_saw():
    llm = ScriptedLLM(CHOICE)
    loop(FakeQS(applied()), llm).step()
    ctx = json.loads(llm.prompts[0])
    assert ctx["role"] == "investigator" and ctx["agent"] == "inv-a"
    assert ctx["rules"]["ClaimTask"]["tuples"] == [{"index": 0, "task": "t1", "incident": "i1"}]
    assert llm.prompts[0] == json.dumps(ctx, sort_keys=True, separators=(",", ":"))


def test_recorded_llm_replays_by_prompt_hash_and_fails_on_a_miss():
    store: dict = {}
    inner = ScriptedLLM("one", "two")
    recording = RecordingLLM(inner, store)
    assert (recording.complete("p1"), recording.complete("p2")) == ("one", "two")
    assert store == {prompt_hash("p1"): "one", prompt_hash("p2"): "two"}
    replay = RecordedLLM(store)
    assert replay.complete("p2") == "two" and replay.calls == 1
    with pytest.raises(MissingRecording):
        replay.complete("never recorded")
    assert recording.complete("p1") == "one" and len(inner.prompts) == 2  # a recorded prompt never reaches the model
