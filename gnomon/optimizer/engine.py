"""The Optimizer run (master §16.1): sample the workload, trial candidate indexes on scratch copies, score them in
integer µs, and commit the winner as a decided IndexChange (M1). It never runs DDL on live; the Reconciler does, and
`resume` carries the run through VERIFY and CLEANUP once M2 has settled the index.

    IDLE → SAMPLE → BASELINE → DETECT → GENERATE → TRIAL → SCORE → DECIDE → COMMIT → AWAIT_SETTLE → VERIFY
         → CLEANUP → COOLDOWN, with RETIRE_CHECK where a window has nothing to do or no winner. D4 (unused optimizer
    indexes) is evaluated in DETECT, so a drop candidate goes through GENERATE and TRIAL like a create (DECISIONS D-63).
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import redis

from gnomon.executive import envelope
from gnomon.kernel.outcomes import Code, Outcome
from gnomon.optimizer import cost, detect, scorer, trials, workload
from gnomon.optimizer.config import OptConfig
from gnomon.optimizer.trials import Measurer, TrialError

ENTRIES = """MATCH (x:IndexEntry)
RETURN x.key AS key, x.entity AS entity, x.label AS label, x.prop AS prop, x.state AS state, x.origin AS origin,
       x.op AS op, x.settled_pt AS settled_pt
ORDER BY key"""
CONSTRAINT_PROPS = "MATCH (x:ConstraintEntry) RETURN x.entity, x.label, x.props ORDER BY x.key"
WRITE_MIX = """MATCH (r:Rewrite) WHERE r.pt >= $since AND r.pt <= $until
RETURN r.rule AS rule, count(*) AS n ORDER BY rule"""
RULE_WRITES = "MATCH (ru:Rule) RETURN ru.name AS name, ru.writes AS writes ORDER BY name, ru.version"


@dataclass
class Run:
    id: str
    state: str = "IDLE"
    trace: list[str] = field(default_factory=list)
    workload: dict | None = None
    candidates: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    winner: dict | None = None
    envelope: dict | None = None
    outcome: Outcome | None = None
    verify: dict | None = None
    base_key: str | None = None
    measurements: dict | None = None
    shapes: list[dict] = field(default_factory=list)  # [{shape_sha, text, param_sets}] of the measured shapes


def touches(cand, writes: Sequence[str]) -> bool:
    """Does a rule with these declared writes touch the elements the candidate index covers (L.p or R.*)?"""
    if cand.entity == "edge":
        return any(w == cand.label or w.startswith(cand.label + ".") for w in writes)
    return any(w in (cand.label, f"{cand.label}.{cand.prop}") for w in writes)


class Optimizer:
    def __init__(self, db, client, kernel, *, agent: str = "optimizer", mode: str = "live",
                 clock: Callable[[], int], config: OptConfig | None = None, measurer: Measurer | None = None,
                 observe: Callable[[str, Run], None] | None = None, before_commit: Callable[[Run], None] | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.db, self.client, self.kernel = db, client, kernel
        self.agent, self.mode, self.clock = agent, mode, clock
        self.config = config or OptConfig()
        self.measurer = measurer or Measurer()
        self.observe, self.before_commit, self.sleep = observe, before_commit, sleep
        self.cooldown_until = 0
        self.live = kernel.graph.name

    # ── state machine
    def _to(self, run: Run, state: str) -> None:
        run.state = state
        run.trace.append(state)
        if self.observe:
            self.observe(state, run)

    def _prefix(self, run: Run) -> str:
        return f"{self.live}:opt:{run.id}:"

    def _cleanup(self, run: Run) -> None:
        trials.delete_all(self.client, self._prefix(run))

    def _finish(self, run: Run) -> Run:
        self._to(run, "CLEANUP")
        self._cleanup(run)
        self._to(run, "COOLDOWN")
        self.cooldown_until = self.clock() + self.config.cooldown_ms
        return run

    def _nothing_to_do(self, run: Run) -> Run:
        self._to(run, "RETIRE_CHECK")
        return self._finish(run)

    # ── the run
    def run(self, commit: bool = True, extra_candidates: Sequence[detect.Candidate] = ()) -> Run:
        now = self.clock()
        if now < self.cooldown_until:
            return Run("cooldown", state="COOLDOWN", trace=["COOLDOWN"])
        run = Run(f"r{self.kernel.head()['seq']}")
        self._to(run, "IDLE")
        try:
            return self._pipeline(run, now, commit, list(extra_candidates))
        except Exception:
            self._cleanup(run)
            raise

    def _pipeline(self, run: Run, now: int, commit: bool, extra: list[detect.Candidate]) -> Run:
        cfg = self.config
        self._to(run, "SAMPLE")
        sampled = workload.sample(self.client, self.live, now, cfg)
        if not sampled.shapes:
            self._to(run, "IDLE")
            return run
        self._to(run, "BASELINE")
        base_key = run.base_key = self._prefix(run) + "base"
        base_seq = self._snapshot(base_key)
        base = self.db.select_graph(base_key)
        measured, base_read, before, profiles = [], {}, {}, {}
        for ws in sampled.shapes:
            try:
                samples = []
                for params in ws.param_sets:
                    samples += self.measurer.read(base, ws.text, params, cfg.warmup, cfg.runs)
                profiles[ws.sha] = trials.profile(self.client, base_key, ws.text, ws.param_sets[0])
                before[ws.sha] = trials.explain(self.client, base_key, ws.text)
            except (redis.ResponseError, TrialError) as exc:
                run.skipped.append({"shape_sha": ws.sha, "error": str(exc)[:200]})
                continue
            base_read[ws.sha] = samples
            measured.append(ws)
        run.shapes = [{"shape_sha": s.sha, "text": s.text, "param_sets": s.param_sets} for s in measured]
        order = [s.sha for s in measured]
        entries = [dict(zip(("key", "entity", "label", "prop", "state", "origin", "op", "settled_pt"), r))
                   for r in base.ro_query(ENTRIES).result_set]

        self._to(run, "DETECT")
        found: dict[str, detect.Candidate] = {}
        targets: dict[str, list[str]] = {}
        for ws in measured:
            for c in detect.candidates(ws.text, profiles[ws.sha], cfg):
                found.setdefault(c.key, c)
                targets.setdefault(c.key, []).append(ws.sha)
        optimizer_indexes = [e for e in entries if e["origin"] == "optimizer" and e["state"] == "operational"]
        state_key = f"{self.live}:optstate"
        state = {k: int(v) for k, v in self.client.hgetall(state_key).items()}
        per_scope = Counter((e["entity"], e["label"]) for e in entries if e["state"] == "operational")
        shared = frozenset(scope for scope, n in per_scope.items() if n >= 2)
        nominees, state = detect.drop_candidates(optimizer_indexes, [before[s] for s in order], state, cfg, shared)
        self.client.delete(state_key)
        if state:
            self.client.hset(state_key, mapping=state)
        for c in [*nominees, *extra]:
            found.setdefault(c.key, c)
            targets.setdefault(c.key, list(order))
        if not found:
            return self._nothing_to_do(run)

        self._to(run, "GENERATE")
        constraint_ix = {f"{e}:{label}:{p}" for e, label, props in base.ro_query(CONSTRAINT_PROPS).result_set
                         for p in props}
        n_opt = len(optimizer_indexes)
        kept, dropped = detect.filter_candidates([found[k] for k in sorted(found)], entries, n_opt, now, cfg,
                                                 constraint_ix)
        reasons = {c.key: r for c, r in dropped}
        rows = []
        for k, key in enumerate(sorted(found), 1):
            c = found[key]
            params = {"op": c.op, "entity": c.entity, "label": c.label, "prop": c.prop, "key": c.key}
            row = {"k": k, "rule": "IndexChange", "key": c.key, "op": c.op, "detect": c.rule, "params": params,
                   "params_sha": envelope.params_sha(params), "targets": targets[key]}
            if key in reasons:
                row.update(outcome="filtered", reason=reasons[key])
            rows.append(row)
        run.candidates = [self._no_change(), *rows]
        trialled = [r for r in rows if "outcome" not in r]
        if not trialled:
            return self._nothing_to_do(run)

        self._to(run, "TRIAL")
        mix_all = self._write_mix(base, now - cfg.window_ms, now)
        measurements = run.measurements = {"base": {"read": base_read, "write": {}}, "candidates": {},
                                           "config": cfg.as_dict()}
        used_mix: dict[str, int] = {}
        for row in trialled:
            self._trial(run, row, found[row["key"]], base_key, order, before, measured, base_read, mix_all,
                        measurements, used_mix)

        self._to(run, "SCORE")  # scores were computed with each trial from its recorded samples; here they are final
        eligible = [r for r in trialled if r.get("outcome") == "scored"
                    and cost.accept(True, r["score_num"], cfg.margin_us)]
        self._to(run, "DECIDE")
        winner = min(eligible, key=lambda r: (-r["score_num"], r["params_sha"]), default=None)
        if winner is None:
            return self._nothing_to_do(run)
        winner["won"] = True
        run.winner = winner
        wl = {**sampled.as_dict(), "shapes": [s.as_dict() for s in measured], "write_mix": dict(sorted(used_mix.items()))}
        run.workload = wl
        run.envelope = self._envelope(run, base_seq, wl, measurements, before)
        if not commit:
            run.state = "DECIDED"
            self._cleanup(run)
            return run
        self._to(run, "COMMIT")
        if self.before_commit:
            self.before_commit(run)
        view_keys = sorted({f"Index:{r['key']}" for r in rows} | {"Index:*"})
        rec_pt = self.clock() if self.mode != "live" else None
        run.outcome = self.kernel.submit(
            "IndexChange", envelope.params_for(winner["params"], run.envelope), pid=envelope.pid_of(run.envelope["did"]),
            agent=self.agent, read_seq=base_seq, view_keys=view_keys, attach=envelope.attach_of(run.envelope),
            mode=self.mode, rec_pt=rec_pt, rec_lc=0 if rec_pt is not None else None)
        if run.outcome.code != Code.APPLIED:
            return self._finish(run)
        self._to(run, "AWAIT_SETTLE")
        return run

    # ── pieces
    @staticmethod
    def _no_change() -> dict:
        return {"k": 0, "rule": "NoChange", "params": {}, "params_sha": envelope.params_sha({}), "outcome": "scored",
                "benefit": 0, "harm": 0, "teams_new": 0, "score_num": 0, "won": False}

    def _snapshot(self, base_key: str) -> int:
        """GRAPH.COPY of live, verified: gapless and chained to Head (§15.2); one retry."""
        from gnomon.executive.branches import BranchManager

        manager = BranchManager(self.db, self.client, self.live)
        for _ in range(2):
            trials.copy(self.client, self.live, base_key)
            base = manager.verify(base_key)
            if base.ok:
                return base.seq
        raise TrialError(f"base snapshot {base_key} failed VERIFY-BASE twice")

    def _write_mix(self, base, since: int, until: int) -> dict[str, tuple[int, list[str]]]:
        counts = base.ro_query(WRITE_MIX, {"since": since, "until": until}).result_set
        writes = {name: list(w or []) for name, w in base.ro_query(RULE_WRITES).result_set}
        return {rule: (n, writes.get(rule, [])) for rule, n in counts if rule in writes}

    def _trial(self, run, row, cand, base_key, order, before, measured, base_read, mix_all, measurements,
               used_mix) -> None:
        cfg = self.config
        k = row["k"]
        prefix = self._prefix(run)
        shadow = f"{prefix}{k}"
        freq = {s.sha: s.freq for s in measured}
        try:
            trials.copy(self.client, base_key, shadow)
            _, build_us = self.measurer.time(lambda: trials.apply_ddl(
                self.db, self.client, shadow, cand, cfg.ddl_timeout_ms, self.sleep))
            after = {sha: trials.explain(self.client, shadow, s.text) for sha, s in ((x.sha, x) for x in measured)}
            row["plan_before"] = cost.digest([before[s].sha for s in order])
            row["plan_after"] = cost.digest([after[s].sha for s in order])
            changed = [s for s in order if after[s].sha != before[s].sha]
            if cand.op == "create":
                effective = any((cand.entity, cand.label) in after[s].index_scans() for s in row["targets"] if s in changed)
                reason = None if effective else "plan_unchanged"
            else:
                reason = "index_in_use" if changed else None
            if reason:
                row.update(outcome="discarded", reason=reason)
                return
            cand_read = {}
            for ws in measured:
                samples = []
                for params in ws.param_sets:
                    samples += self.measurer.read(self.db.select_graph(shadow), ws.text, params, cfg.warmup, cfg.runs)
                cand_read[ws.sha] = samples
            relevant = {rule: n for rule, (n, writes) in mix_all.items() if touches(cand, writes)}
            cand_write, base_write, unsynth = {}, {}, []
            for rule, n in sorted(relevant.items()):
                size = min(n, cfg.write_cap)
                kw = f"{prefix}{k}w"
                trials.copy(self.client, shadow, kw)
                calls = trials.synthesize(self.db, self.kernel.registry, kw, rule, size, f"{k}")
                if calls is None:
                    unsynth.append(rule)
                    continue
                cand_write[rule] = self.measurer.write(calls, self.db.select_graph(kw))
                if rule not in measurements["base"]["write"]:
                    bw = f"{prefix}basew"
                    trials.copy(self.client, base_key, bw)
                    calls = trials.synthesize(self.db, self.kernel.registry, bw, rule, size, "base")
                    measurements["base"]["write"][rule] = self.measurer.write(calls or [], self.db.select_graph(bw))
                base_write[rule] = measurements["base"]["write"][rule]
                used_mix[rule] = n
            trials.remove(self.client, f"{prefix}{k}w")
            trials.remove(self.client, f"{prefix}basew")
            record = {"build_us": build_us, "read": cand_read, "write": cand_write, "unsynthesized": unsynth}
            measurements["candidates"][str(k)] = record
            wfreq = {rule: n for rule, n in relevant.items()}
            if cand.op == "create":
                score = cost.score(base_read, cand_read, freq, base_write, cand_write, wfreq, build_us, cfg)
            else:
                score = cost.score_drop(base_read, cand_read, freq, base_write, cand_write, wfreq, cfg)
            row.update(outcome="scored", benefit=score.gain_us, harm=score.write_us + score.build_amort_us + score.mem_us,
                       teams_new=0, score_num=score.net_us, measure_digest=cost.digest(record), won=False,
                       cost=score.as_dict())
        except TrialError as exc:
            row.update(outcome="error", reason=str(exc)[:200])
        finally:
            trials.remove(self.client, shadow)

    def _envelope(self, run, base_seq, wl, measurements, before) -> dict:
        winner = run.winner
        targets = winner["targets"]
        evidence = []
        shape_after = self._explain_after(run, winner, targets)
        for sha in targets:
            for role, plan in (("plan_before", before[sha]), ("plan_after", shape_after[sha])):
                evidence.append({"ord": len(evidence), "algo": "EXPLAIN", "role": role, "path": plan.ops(), "cites": []})
        did = f"D-opt-{base_seq}"
        view_keys = sorted({f"Index:{r['key']}" for r in run.candidates if r.get("key")} | {"Index:*"})
        return envelope.seal({
            "did": did, "attempt": 1, "aid": f"{did}/1", "kind": "index", "proposer": "optimizer",
            "rationale_ref": f"opt:{run.id}", "scorer": scorer.NAME, "scorer_sha": scorer.sha(), "read_seq": base_seq,
            "hops": 0, "weight_scale": 1, "lambda_n": 1, "lambda_d": 1, "tau": 0, "margin": self.config.margin_us,
            "baseline": {"plans": {sha: p.sha for sha, p in before.items()}}, "candidates": run.candidates,
            "evidence": evidence, "cites": [], "footprint_digest": cost.digest(view_keys),
            "workload_digest": cost.digest(wl), "channels": {}, "workload": wl, "measurements": measurements})

    def _explain_after(self, run, winner, targets) -> dict:
        """The winner's plans after its change, recorded in the evidence (re-derived on a throwaway copy)."""
        shadow = f"{self._prefix(run)}ev"
        cand = detect.candidate_of(winner["detect"], winner["params"]["entity"], winner["params"]["label"],
                                   winner["params"]["prop"], winner["op"])
        try:
            trials.copy(self.client, run.base_key, shadow)
            trials.apply_ddl(self.db, self.client, shadow, cand, self.config.ddl_timeout_ms, self.sleep)
            texts = {s["shape_sha"]: s["text"] for s in run.shapes}
            return {sha: trials.explain(self.client, shadow, texts[sha]) for sha in targets}
        finally:
            trials.remove(self.client, shadow)

    # ── after the reconciler
    def resume(self, run: Run) -> Run:
        """AWAIT_SETTLE → VERIFY → CLEANUP → COOLDOWN once M2 has settled the index (a no-op until then)."""
        if run.state != "AWAIT_SETTLE":
            return run
        winner = run.winner
        rows = self.kernel.read("MATCH (x:IndexEntry {key: $k}) RETURN x.state, x.op", {"k": winner["key"]})
        state, op = rows[0]
        if state in ("requested", "retiring"):
            return run
        settled = (op == "create" and state == "operational") or (op == "drop" and state == "retired")
        if not settled:
            return self._finish(run)
        self._to(run, "VERIFY")
        shapes = []
        for sha in winner["targets"]:
            text = next(s["text"] for s in run.shapes if s["shape_sha"] == sha)
            live = trials.explain(self.client, self.live, text)
            used = (winner["params"]["entity"], winner["params"]["label"]) in live.index_scans()
            shapes.append({"shape_sha": sha, "plan_sha": live.sha, "index_used": used,
                           "ok": used if winner["op"] == "create" else True})
        run.verify = {"ok": all(s["ok"] for s in shapes), "shapes": shapes}
        return self._finish(run)
