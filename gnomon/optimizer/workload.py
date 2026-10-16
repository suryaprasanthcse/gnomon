"""Workload telemetry and sampling (master §16.2): non-authoritative, kept in the Redis stream `<graph key>:wl`.

The query service appends one entry per agent read. Sampling takes the top OPT_TOP_N shapes by total internal time over
the window and, for each, the OPT_PARAM_SETS most recent distinct parameter sets with the shape's frequency.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from gnomon.kernel.jcs import canonicalize
from gnomon.optimizer import cost, shapes
from gnomon.optimizer.config import OptConfig

STREAM_MAXLEN = 200_000


def stream_key(graph_key: str) -> str:
    return f"{graph_key}:wl"


class Telemetry:
    """Appends read entries for one tenant graph; time comes from the injected clock (contract C8)."""

    def __init__(self, client, graph_key: str, clock: Callable[[], int]) -> None:
        self.client, self.key, self.clock = client, stream_key(graph_key), clock

    def record(self, cypher: str, params: Mapping | None, internal_ms: float, rows: int, caller: str) -> None:
        shape = shapes.normalize(cypher, params)
        self.client.xadd(self.key, {
            "shape_sha": shape.sha, "cypher": shape.text, "params": canonicalize(shape.params),
            "internal_ms": repr(float(internal_ms)), "rows": str(int(rows)), "pt": str(int(self.clock())),
            "caller": caller}, maxlen=STREAM_MAXLEN, approximate=True)


@dataclass
class WShape:
    sha: str
    text: str
    freq: int
    total_us: int
    param_sets: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"shape_sha": self.sha, "cypher": self.text, "freq": self.freq, "total_us": self.total_us,
                "param_sets": self.param_sets}


@dataclass
class Workload:
    shapes: list[WShape]
    since: int
    until: int

    def as_dict(self) -> dict:
        return {"shapes": [s.as_dict() for s in self.shapes], "window": {"since": self.since, "until": self.until}}

    @property
    def digest(self) -> str:
        return cost.digest(self.as_dict())


def sample(client, graph_key: str, now: int, config: OptConfig) -> Workload:
    since = now - config.window_ms
    groups: dict[str, list[Mapping]] = {}
    for _id, f in client.xrange(stream_key(graph_key)):
        if since <= int(f["pt"]) <= now:
            groups.setdefault(f["shape_sha"], []).append(f)
    ranked = sorted(((sum(round(float(f["internal_ms"]) * 1000) for f in fs), sha) for sha, fs in groups.items()),
                    key=lambda t: (-t[0], t[1]))[:config.top_n]
    out = []
    for total, sha in ranked:
        entries = groups[sha]
        seen: list[str] = []
        for f in reversed(entries):
            if f["params"] not in seen:
                seen.append(f["params"])
        out.append(WShape(sha, entries[-1]["cypher"], len(entries), total,
                          [json.loads(p) for p in seen[:config.param_sets]]))
    return Workload(out, since, now)
