"""K4 · replay equality (master §12.1–§12.2, invariant I1): the canonical hash of the live graph equals that of
its own log replayed into a scratch graph. On a difference it reports the first differing canonical line,
which names the element and its properties; a replay that cannot reproduce a step reports the divergence.
The scratch graph is deleted afterwards.
"""

from __future__ import annotations

import redis

from gnomon.audit import CheckResult
from gnomon.audit.canonical import differing_properties, first_difference, graph_lines, hash_lines
from gnomon.audit.replay import Divergence, replay


def check(db, client: redis.Redis, key: str, *, target: str | None = None) -> CheckResult:
    graph = db.select_graph(key)
    head = graph.ro_query("MATCH (h:Head) RETURN h.seq").result_set
    seq = head[0][0] if head else 0
    target = target or f"{key}:replay:{seq}"
    try:
        try:
            result = replay(db, client, key, until=seq, target=target)
        except Divergence as exc:
            return CheckResult("K4", [{"problem": "divergence", **exc.as_dict()}])
        live, again = graph_lines(graph), graph_lines(db.select_graph(result.key))
    finally:
        if client.exists(target):
            client.execute_command("GRAPH.DELETE", target)
    live_hash, replay_hash = hash_lines(live), hash_lines(again)
    if live_hash == replay_hash:
        return CheckResult("K4")
    left, right = first_difference(live, again)
    return CheckResult("K4", [{"problem": "state_differs", "live": left, "replay": right,
                               "properties": differing_properties(left, right), "live_hash": live_hash,
                               "replay_hash": replay_hash}])
