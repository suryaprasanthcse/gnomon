"""K7 steps 2-7 (master §15.13): re-simulate Executive decisions from the log.

For every decision whose scorer is a `<library>.blastRadius`: rebuild the base by replaying the log up to the decision's
`read_seq` and verify it, check the scorer library hash, an active Scorer at the commit and the configuration, simulate and
score every recorded candidate again on its own branch and compare outcome, reason and the integer scores exactly,
recompute the winner (a recorded `vetoed` candidate is excluded, and must be backed by a veto), the footprint and its
digest, and the evidence and cites. A decision made by any other scorer is left to steps 1 and 8 in k7.py.
"""

from __future__ import annotations

import json

EXECUTIVE_DECISIONS = """MATCH (x:Decision)-[:COMMITTED_AS]->(r:Rewrite)
WHERE x.kind = 'action' AND x.scorer ENDS WITH '.blastRadius'
RETURN x.id AS id, r.id AS rewrite, r.seq AS seq, r.attach AS attach, r.view_keys AS view_keys
ORDER BY seq"""
ACTIVE_SCORER = """MATCH (s:Scorer {name: $name, sha: $sha})
WHERE s.registered_seq <= $seq AND (s.deactivated_seq IS NULL OR s.deactivated_seq > $seq)
RETURN count(s), max(s.version)"""
RULES_AT = """MATCH (ru:Rule) WHERE ru.registered_seq <= $seq AND (ru.deactivated_seq IS NULL OR ru.deactivated_seq > $seq)
RETURN properties(ru) ORDER BY ru.name"""
EXECUTIVE_AGENT = "MATCH (a:Agent) WHERE a.role = 'executive' AND a.active = true RETURN a.id ORDER BY a.id LIMIT 1"
VETOES = "MATCH (a:Approval {did: $did}) WHERE a.verdict = 'veto' RETURN a.rule ORDER BY a.rule"
METRICS = ("benefit", "harm", "teams_new", "score_num")


def resimulate(graph, db, client) -> list[dict]:
    from gnomon.audit.replay import Divergence, replay, rule_from_node
    from gnomon.executive import engine, envelope, scoring
    from gnomon.executive.branches import BranchManager
    from gnomon.kernel.registry import Registry
    from gnomon.udf import loader

    findings: list[dict] = []
    key = graph.name
    manager = BranchManager(db, client, key)
    for did, rewrite, seq, attach, view_keys in graph.ro_query(EXECUTIVE_DECISIONS).result_set:
        d = json.loads(attach)

        def find(problem: str, **detail) -> None:
            findings.append({"problem": problem, "id": did, "rewrite": rewrite, **detail})

        library = d["scorer"].rsplit(".", 1)[0]
        try:
            loaded = loader.library_sha(client, library)
        except LookupError:
            find("library_missing", library=library)
        else:
            if loaded != d["scorer_sha"]:
                find("library_sha", library=library, loaded=loaded, recorded=d["scorer_sha"])
        active, version = graph.ro_query(ACTIVE_SCORER, {"name": d["scorer"], "sha": d["scorer_sha"],
                                                         "seq": seq}).result_set[0]
        if not active:
            find("scorer_inactive", scorer=d["scorer"])
        federation = (version or 1) >= 2  # scorer v2 is the federated blast radius (§18)
        config = {"hops": scoring.HOPS, "weight_scale": scoring.W, "lambda_n": scoring.LAMBDA_N,
                  "lambda_d": scoring.LAMBDA_D, "tau": scoring.TAU, "margin": scoring.MARGIN}
        bad = sorted(name for name, value in config.items() if d.get(name) != value)
        if bad:
            find("config_mismatch", fields=bad)
        target = f"{key}:sim:k7:{did}:base"
        try:
            try:
                replay(db, client, key, until=d["read_seq"], target=target)
            except Divergence as exc:
                find("base_divergence", detail=exc.as_dict())
                continue
            base = manager.verify(target)
            if not base.ok:
                find("base_unverified", gapless=base.gapless, linked=base.linked)
                continue
            base_graph = db.select_graph(target)
            registry = Registry([rule_from_node(props) for (props,) in
                                 base_graph.ro_query(RULES_AT, {"seq": base.seq}).result_set])
            agents = base_graph.ro_query(EXECUTIVE_AGENT).result_set
            if not agents:
                find("no_executive_agent")
                continue
            recorded = d["candidates"]
            incident = recorded[0]["params"]["incident"]
            run = engine.run(db, client, registry, base, f"k7-{did}", d["attempt"], incident,
                             [{"k": c["k"], "rule": c["rule"], "params": c["params"]} for c in recorded],
                             library, agents[0][0], d["hops"], keep=False, federation=federation)
            again = engine.candidate_rows(run.results, {c["params_sha"] for c in recorded if c["outcome"] == "vetoed"})
            for was, now in zip(recorded, again):
                differs = [f for f in ("outcome", "reason", *METRICS) if was.get(f) != now.get(f)]
                if differs:
                    find("resim_mismatch", k=was["k"], fields=differs)
            if [c["k"] for c in recorded if c["won"]] != [c["k"] for c in again if c["won"]]:
                find("winner_mismatch")
            vetoed_rules = sorted(c["rule"] for c in recorded if c["outcome"] == "vetoed")
            backing = [r for (r,) in graph.ro_query(VETOES, {"did": did}).result_set]
            if any(vetoed_rules.count(r) > backing.count(r) for r in set(vetoed_rules)):
                find("veto_unbacked", rules=vetoed_rules)
            remotes = engine.remotes_of(base_graph, run.base_out["region"]) if federation else {}
            footprint = scoring.footprint(run.base_out, incident, federation, remotes)
            if engine.digest(footprint) != d["footprint_digest"]:
                find("footprint_mismatch")
            channels = envelope.channel_positions(base_graph) if federation else {}
            if channels != d.get("channels"):
                find("channels_mismatch", recorded=d.get("channels"), rebuilt=channels)
            missing = sorted(set(footprint) - set(view_keys or ()))
            if missing:
                find("footprint_not_in_view_keys", missing=missing[:5])
            won = next(c["k"] for c in again if c["won"])
            winner = next(r for r in run.results if r.k == won)
            evidence, cites = ([], []) if winner.out is None else scoring.evidence(run.base_out, winner.out, d["scorer"])
            if evidence != d["evidence"] or cites != d["cites"]:
                find("evidence_mismatch")
            allowed = {(c["s"], c["fid"]) for c in d["cites"]}
            if not {(c["s"], c["fid"]) for e in d["evidence"] for c in e["cites"]} <= allowed:
                find("cites_not_covered")
        finally:
            manager.retire([*manager.sim_graphs(f"k7:{did}"), target])
    return findings
