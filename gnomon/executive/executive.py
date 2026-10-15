"""The Counterfactual Executive (master §15): deterministic, trusted, no LLM, and never a writer of live.

`decide` runs the §15.11 state machine: snapshot the live graph, enumerate the enabled actions on that base, simulate
each candidate on its own branch, score the futures with Exec.blastRadius, pick the winner in integer arithmetic,
seal the envelope and commit the winner through the kernel with P1 and E0. A gated winner waits for a human in
AWAITING; `resume` carries on from the approval. The kernel is reached only through proposals.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from gnomon.executive import engine, envelope, scoring
from gnomon.executive.branches import Base, BranchManager, SnapshotError
from gnomon.kernel.outcomes import Code, Outcome

FLUSH_LIMIT = 6  # a decision that keeps being flushed is failed rather than looped forever
APPROVAL_TTL_MS = 900_000


@dataclass
class Decision:
    """The caller's view of one decision, and the memory the Executive resumes it from."""

    did: str
    pid: str
    request_id: str
    incident: str
    attempt: int = 1
    state: str = "NOMINATED"
    trace: list[str] = field(default_factory=lambda: ["NOMINATED"])
    outcome: Outcome | None = None
    envelope: dict | None = None
    candidates: list[dict] = field(default_factory=list)
    winner: dict | None = None
    mode: str = "exhaustive"
    dropped: list[dict] = field(default_factory=list)
    stale_flushes: int = 0
    flushes: int = 0
    error: str | None = None
    base_output: dict | None = None
    # internal
    nominations: list[dict] = field(default_factory=list)
    rationale: str = ""
    proposer: str = ""
    base: Base | None = None
    run: engine.Run | None = None
    keys: list[str] = field(default_factory=list)
    tuple_keys: dict = field(default_factory=dict)
    vetoed: set[str] = field(default_factory=set)
    commit_params: dict | None = None
    view_keys: list[str] = field(default_factory=list)
    graphs: list[str] = field(default_factory=list)

    def at(self, state: str) -> None:
        self.state = state
        self.trace.append(state)


class Executive:
    def __init__(self, db, client, kernel, *, agent: str = "executive", mode: str = "live", library: str = "Exec",
                 clock: Callable[[], int] | None = None, k_max: int = scoring.K_MAX, hops: int = scoring.HOPS,
                 stale_retries: int = scoring.STALE_RETRIES, approval_ttl_ms: int = APPROVAL_TTL_MS,
                 before_commit: Callable | None = None) -> None:
        if mode != "live" and clock is None:
            raise ValueError(f"mode {mode!r} needs a clock")
        self.db, self.client, self.kernel = db, client, kernel
        self.agent, self.mode, self.library, self.clock = agent, mode, library, clock
        self.k_max, self.hops, self.stale_retries = k_max, hops, stale_retries
        self.approval_ttl_ms, self.before_commit = approval_ttl_ms, before_commit
        self.branches = BranchManager(db, client, kernel.graph.name)
        self.decisions: dict[str, Decision] = {}

    # ── pieces (public so the tests and K7 can drive them one at a time) ───────────────────────────────────────────
    def snapshot(self, did: str, attempt: int) -> Base:
        return self.branches.snapshot(did, attempt)

    def verify(self, key: str) -> Base:
        return self.branches.verify(key)

    def branch(self, base: Base, k: int) -> str:
        return self.branches.branch(base, k)

    def retire(self, keys) -> None:
        self.branches.retire(keys)

    def sim(self, base: Base, k: int, rule: str, params: Mapping, keep: bool = False, did: str = "D-sim",
            attempt: int = 1) -> engine.Result:
        branch = self.branches.branch(base, k)
        result = engine.sim_apply(self.db, self.kernel.registry, base, branch, did, attempt, k, rule, params, self.agent)
        if not keep:
            self.branches.retire([branch])
            result.branch = None
        return result

    # ── requests ─────────────────────────────────────────────────────────────────────────────────────────────────
    @staticmethod
    def did_of(incident: str, request_id: str) -> str:
        return "D-" + hashlib.sha256(f"{incident}:{request_id}".encode("utf-8")).hexdigest()[:12]

    def simulate(self, incident: str, nominations: Sequence[Mapping] = (), rationale: str = "",
                 request_id: str = "sim", proposer: str = "agent:simulate") -> Decision:
        """The full pipeline without a commit: the futures, their integer scores and the would-be envelope."""
        d = self._new(incident, nominations, rationale, request_id, proposer)
        try:
            self._evaluate(d)
        except SnapshotError as exc:
            self._fail(d, str(exc))
        else:
            d.at("SIMULATED")
        self._retire(d)
        return d

    def decide(self, incident: str, nominations: Sequence[Mapping] = (), rationale: str = "",
               request_id: str = "r1", proposer: str = "agent:responder") -> Decision:
        d = self._new(incident, nominations, rationale, request_id, proposer)
        done = self.kernel.read("MATCH (r:Rewrite {id: $pid}) RETURN r.seq, r.hash", {"pid": d.pid})
        if done:  # a re-issued request after a crash: the earlier attempt already committed
            d.outcome = Outcome(Code.DUPLICATE, seq=done[0][0], hash=done[0][1])
            d.at("DONE")
            return d
        if not self.kernel.read("MATCH (i:Incident {id: $i}) WHERE i.state = 'open' RETURN i.id", {"i": incident}):
            return self._fail(d, f"incident {incident!r} is not open")
        self.decisions[d.did] = d
        return self._drive(d)

    def resume(self, did: str) -> Decision:
        """Carry an AWAITING decision on from what the human did with its approval."""
        d = self.decisions[did]
        if d.state != "AWAITING":
            return d
        rows = self.kernel.read("MATCH (a:Approval {aid: $a}) RETURN a.state, a.verdict, a.expires_pt",
                                {"a": d.envelope["aid"]})
        state, verdict, expires = rows[0] if rows else ("missing", None, 0)
        if state == "decided" and verdict == "approve":
            return self._commit(d)
        if state == "decided":  # veto: mark the winner vetoed and select the next best
            d.vetoed.add(d.winner["params_sha"])
            d.attempt += 1
            d.at("SELECT")
            return self._seal(d)
        if self._now() >= (expires or 0):  # expired: flush and ask again with attempt + 1
            return self._flush(d, stale=False)
        return d

    # ── internals ────────────────────────────────────────────────────────────────────────────────────────────────
    def _now(self) -> int:
        return self.clock() if self.clock else 0

    def _new(self, incident, nominations, rationale, request_id, proposer) -> Decision:
        did = self.did_of(incident, request_id)
        return Decision(did=did, pid=envelope.pid_of(did), request_id=request_id, incident=incident,
                        nominations=[dict(n) for n in nominations], rationale=rationale, proposer=proposer)

    def _fail(self, d: Decision, error: str) -> Decision:
        d.error = error
        d.at("FAILED")
        self._retire(d)
        return d

    def _retire(self, d: Decision) -> None:
        self.branches.retire(self.branches.sim_graphs(d.did))
        d.graphs = []

    def _drive(self, d: Decision) -> Decision:
        try:
            self._evaluate(d)
        except SnapshotError as exc:
            return self._fail(d, str(exc))
        return self._seal(d)

    def _evaluate(self, d: Decision) -> None:
        """SNAPSHOT, ENUMERATE, BRANCH, SIMULATE, SCORE: the futures of attempt `d.attempt` (or SIMULATED's dry run)."""
        d.at("SNAPSHOT")
        d.base = self.snapshot(d.did, d.attempt)
        d.at("ENUMERATE")
        tuples = engine.enumerate_actions(self.db, self.kernel.registry, d.base, d.incident)
        escalate_only = d.stale_flushes >= self.stale_retries
        d.mode, chosen, d.dropped = engine.candidate_set(d.incident, tuples, d.nominations, self.k_max, escalate_only)
        d.tuple_keys = {(t["rule"], tuple(sorted(t["params"].items()))): t["keys"] for t in tuples}
        d.at("BRANCH")
        d.at("SIMULATE")
        d.run = engine.run(self.db, self.client, self.kernel.registry, d.base, d.did, d.attempt, d.incident, chosen,
                           self.library, self.agent, self.hops)
        d.at("SCORE")
        d.base_output = d.run.base_out
        d.keys = [key for t in chosen for key in self.tuple_keys_of(d, t)]
        d.at("SELECT")
        self._select(d)

    def tuple_keys_of(self, d: Decision, candidate: Mapping) -> list[str]:
        return d.tuple_keys.get((candidate["rule"], tuple(sorted(candidate["params"].items()))), [])

    def _select(self, d: Decision) -> None:
        d.candidates = engine.candidate_rows(d.run.results, d.vetoed)
        d.winner = next(c for c in d.candidates if c["won"])

    def _seal(self, d: Decision) -> Decision:
        """ENVELOPE: footprint, evidence, sha; then COMMIT, or PRECHECK and REQUEST for a gated winner."""
        if d.state != "SELECT":
            d.at("SELECT")
        self._select(d)
        winner_result = next(r for r in d.run.results if r.k == d.winner["k"])
        algo = f"{self.library}.blastRadius"
        evidence, cites = ([], []) if winner_result.out is None else scoring.evidence(d.base_output, winner_result.out, algo)
        footprint = scoring.footprint(d.base_output, d.incident)
        d.view_keys = sorted(set(d.keys) | set(footprint))
        d.at("ENVELOPE")
        d.envelope = envelope.seal({
            "did": d.did, "attempt": d.attempt, "aid": f"{d.did}/{d.attempt}", "kind": "action",
            "proposer": d.proposer, "rationale_ref": "llm:" + hashlib.sha256(d.rationale.encode("utf-8")).hexdigest()[:12],
            "scorer": algo, "scorer_sha": self._library_sha(), "read_seq": d.base.seq, "hops": self.hops,
            "weight_scale": scoring.W, "lambda_n": scoring.LAMBDA_N, "lambda_d": scoring.LAMBDA_D, "tau": scoring.TAU,
            "margin": scoring.MARGIN, "baseline": engine.baseline(d.base_output), "candidates": d.candidates,
            "evidence": evidence, "cites": cites, "footprint_digest": engine.digest(footprint),
            "workload_digest": None, "channels": {}})
        d.commit_params = envelope.params_for(d.winner["params"], d.envelope)
        if not self.kernel.registry.get(d.winner["rule"]).gated:
            return self._commit(d)
        d.at("PRECHECK")
        if self._changed(d):
            return self._flush(d, stale=True)
        d.at("REQUEST")
        out = self._propose("RequestApproval", {
            "aid": d.envelope["aid"], "did": d.did, "decision_sha": d.envelope["sha"], "rule": d.winner["rule"],
            "params_sha": envelope.params_sha(d.commit_params),
            "summary": f"{d.winner['rule']} {json.dumps(d.winner['params'], sort_keys=True)}",
            "incident": d.incident, "ttl_ms": self.approval_ttl_ms}, f"req:{d.envelope['aid']}")
        if out.code not in (Code.APPLIED, Code.DUPLICATE):
            return self._fail(d, f"RequestApproval: {out.code.value} {out.detail}")
        d.at("AWAITING")
        return d

    def _changed(self, d: Decision) -> bool:
        rows = self.kernel.read("MATCH (kv:KeyVersion) WHERE kv.key IN $keys AND kv.seq > $seq RETURN count(kv)",
                                {"keys": d.view_keys, "seq": d.base.seq})
        return rows[0][0] > 0

    def _library_sha(self) -> str:
        from gnomon.udf import loader

        return loader.library_sha(self.client, self.library)

    def _propose(self, rule: str, params: Mapping, pid: str, **kw) -> Outcome:
        kw.setdefault("read_seq", None)
        rec_pt = self._now() if self.mode != "live" else None
        return self.kernel.submit(rule, params, pid=pid, agent=self.agent, mode=self.mode, rec_pt=rec_pt,
                                  rec_lc=0 if rec_pt is not None else None, **{k: v for k, v in kw.items() if v is not None})

    def _commit(self, d: Decision) -> Decision:
        d.at("COMMIT")
        if self.before_commit is not None:
            self.before_commit(self, d)
        out = self._propose(d.winner["rule"], d.commit_params, d.pid, read_seq=d.base.seq, view_keys=d.view_keys,
                            attach=envelope.attach_of(d.envelope))
        d.outcome = out
        if out.code in (Code.APPLIED, Code.DUPLICATE):
            d.at("RETIRE")
            self._retire(d)
            d.at("DONE")
            return d
        if out.code in (Code.REJECTED_STALE, Code.REJECTED_GUARD):
            return self._flush(d, stale=out.code == Code.REJECTED_STALE)
        return self._fail(d, f"{out.code.value} {out.detail}")

    def _flush(self, d: Decision, stale: bool) -> Decision:
        """FLUSH: delete this attempt's graphs and start again from SNAPSHOT with attempt + 1."""
        d.at("FLUSH")
        d.flushes += 1
        if stale:
            d.stale_flushes += 1
        if d.flushes > FLUSH_LIMIT:
            return self._fail(d, "too many flushes")
        self._retire(d)
        d.attempt += 1
        d.vetoed = set(d.vetoed)
        return self._drive(d)


def read_decision(graph, did: str) -> dict | None:
    """The committed decision subgraph as a plain value (the `decision` tool): header, candidates, evidence, commit."""
    rows = graph.ro_query("MATCH (x:Decision {id: $d}) OPTIONAL MATCH (x)-[:COMMITTED_AS]->(r:Rewrite) "
                          "RETURN properties(x), r.id, r.seq", {"d": did}).result_set
    if not rows:
        return None
    props, pid, seq = rows[0]
    candidates = [dict(c) for (c,) in graph.ro_query(
        "MATCH (:Decision {id: $d})-[:CONSIDERED]->(c:Candidate) RETURN properties(c) ORDER BY c.k", {"d": did}).result_set]
    evidence = []
    for eprops, cites in graph.ro_query(
            "MATCH (:Decision {id: $d})-[:JUSTIFIED_BY]->(e:Evidence) OPTIONAL MATCH (e)-[c:CITES]->(s:Entity) "
            "WITH e, collect([s.uid, c.fid]) AS cites RETURN properties(e), cites ORDER BY e.ord", {"d": did}).result_set:
        evidence.append({**{k: v for k, v in eprops.items() if k != "rw"}, "cites": [{"s": s, "fid": f} for s, f in cites]})
    header = {k: v for k, v in props.items() if k not in ("rw", "read_seq")}
    return {"did": did, **header, "candidates": [{k: v for k, v in c.items() if k != "cid"} for c in candidates],
            "evidence": evidence, "committed_as": pid, "seq": seq}
