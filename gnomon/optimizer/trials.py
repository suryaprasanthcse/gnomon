"""Shadow trials (master §16.4, §16.7): scratch copies, DDL on them, plan extraction and the measurements.

Only scratch graphs named `<live>:opt:<run>:*` are ever written here. PROFILE and EXPLAIN go through the raw commands
(parameters travel in a CYPHER header); read times are the server's own internal execution time; write times are the
round trip of a sim-mode proposal (the kernel service does not expose the server-side time of a write, DECISIONS D-62).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence

import redis

from gnomon.audit.k8 import physical_indexes
from gnomon.kernel.outcomes import Code
from gnomon.kernel.service import INF, KernelService, Proposal
from gnomon.optimizer import plan as plans
from gnomon.ops.reconciler import create_statement, drop_statement


class TrialError(RuntimeError):
    """A trial that could not run (a shape that fails on the copy, a synthetic write the kernel refused)."""


def literal(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=True)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(literal(v) for v in value) + "]"
    raise TrialError(f"cannot send a parameter of type {type(value).__name__} in a CYPHER header")


def header(params: Mapping) -> str:
    return "CYPHER " + " ".join(f"{k}={literal(v)}" for k, v in sorted(params.items())) + " "


def explain(client, key: str, text: str) -> plans.Plan:
    return plans.parse(client.execute_command("GRAPH.EXPLAIN", key, text))


def profile(client, key: str, text: str, params: Mapping) -> plans.Plan:
    return plans.parse(client.execute_command("GRAPH.PROFILE", key, header(params) + text))


class Measurer:
    """Where the numbers come from: the server's internal read time, and wall time for DDL and writes (µs)."""

    def __init__(self, perf_ns: Callable[[], int] = time.perf_counter_ns) -> None:
        self.perf_ns = perf_ns

    def read(self, graph, text: str, params: Mapping, warmup: int, runs: int) -> list[int]:
        for _ in range(warmup):
            graph.ro_query(text, dict(params))
        return [round(graph.ro_query(text, dict(params)).run_time_ms * 1000) for _ in range(runs)]

    def write(self, calls: Sequence[Callable[[], object]], graph) -> list[int]:
        samples = []
        for call in calls:
            started = self.perf_ns()
            call()
            samples.append((self.perf_ns() - started) // 1000)
        return samples

    def time(self, fn: Callable[[], object]):
        started = self.perf_ns()
        value = fn()
        return value, (self.perf_ns() - started) // 1000


def copy(client, source: str, target: str) -> None:
    if client.exists(target):
        client.execute_command("GRAPH.DELETE", target)
    client.execute_command("GRAPH.COPY", source, target)


def remove(client, name: str) -> None:
    if client.exists(name):
        client.execute_command("GRAPH.DELETE", name)


def delete_all(client, prefix: str) -> None:
    for name in client.execute_command("GRAPH.LIST"):
        if name.startswith(prefix):
            client.execute_command("GRAPH.DELETE", name)


def apply_ddl(db, client, key: str, cand, timeout_ms: int, sleep=time.sleep, monotonic=time.monotonic) -> None:
    """The create or drop of `cand` on scratch graph `key`, then a poll of db.indexes() until it took effect."""
    graph = db.select_graph(key)
    statement = (create_statement if cand.op == "create" else drop_statement)(cand.entity, cand.label, cand.prop)
    try:
        graph.query(statement)
    except redis.ResponseError as exc:
        raise TrialError(f"{statement}: {exc}") from exc
    started = monotonic()
    while True:
        status = physical_indexes(graph).get(cand.key)
        if (cand.op == "create" and status == "OPERATIONAL") or (cand.op == "drop" and status is None):
            return
        if status == "FAILED" or (monotonic() - started) * 1000 >= timeout_ms:
            raise TrialError(f"{statement}: index not ready (status {status})")
        sleep(0.02)


# ── write synthesis (§16.7 step 6): proposals a rule would receive, built from the shadow's own state

def _assert_owned_by(graph, n: int) -> list[dict]:
    owners = graph.ro_query(
        "MATCH (s:Service)-[f:OWNED_BY]->(t:Team) WHERE f.te = $INF AND f.vt = $INF "
        "RETURN s.uid AS s, t.uid AS o ORDER BY s LIMIT $n", {"INF": INF, "n": n}).result_set
    teams = [t for (t,) in graph.ro_query("MATCH (t:Team) RETURN t.uid ORDER BY t.uid").result_set]
    vf = graph.ro_query("MATCH (h:Head) RETURN h.pt").result_set[0][0]
    return [{"s": s, "o": teams[(teams.index(o) + 1) % len(teams)], "vf": vf, "supersede": True, "old_o": o}
            for s, o in owners if len(teams) > 1]


def _register_service(graph, n: int) -> list[dict]:
    return [{"id": f"opt-sim-{i}", "name": f"opt-sim-{i}", "tier": "t1", "criticality": 3, "user_facing": True}
            for i in range(n)]


SYNTHESIZERS: dict[str, Callable] = {"AssertFact.OWNED_BY": _assert_owned_by, "RegisterEntity.Service": _register_service}


def synthesize(db, registry, key: str, rule: str, n: int, tag: str) -> list[Callable[[], object]] | None:
    """n callables, each applying one sim-mode proposal of `rule` to scratch graph `key`; None: no synthesizer."""
    maker = SYNTHESIZERS.get(rule)
    if maker is None:
        return None
    graph = db.select_graph(key)
    seq, pt = graph.ro_query("MATCH (h:Head) RETURN h.seq, h.pt").result_set[0]
    roles = list(registry.get(rule).caller)
    agents = graph.ro_query("MATCH (a:Agent) WHERE a.active = true AND a.role IN $roles RETURN a.id ORDER BY a.id LIMIT 1",
                            {"roles": roles}).result_set
    if not agents:
        return []
    service = KernelService(graph, registry)
    calls = []
    for i, params in enumerate(maker(graph, n)):
        proposal = Proposal(rule=rule, p=params, pid=f"sim:opt:{tag}:{rule}:{i}", agent=agents[0][0], read_seq=seq,
                            view_keys=(), mode="sim", rec_pt=pt + 10 * (i + 1), rec_lc=0)
        calls.append(lambda proposal=proposal: _apply(service, proposal))
    return calls


def _apply(service: KernelService, proposal: Proposal):
    outcome = service.submit(proposal)
    if outcome.code != Code.APPLIED:
        raise TrialError(f"synthetic {proposal.rule} was refused: {outcome.code.value} {outcome.detail}")
    return outcome
