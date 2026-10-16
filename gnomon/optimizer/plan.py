"""Plan parsing (master §16.3): GRAPH.PROFILE / GRAPH.EXPLAIN output as a tree, and the plan sha.

    line := indent op_name (" | " argument)? (" | Records produced: <int>, Execution time: <float> ms)?
    plan_sha := SHA-256 of "<depth>|<op>|<argument>" lines, aliases renamed a0, a1, ... by first appearance
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

METRICS = re.compile(r"^Records produced: (\d+), Execution time: ([\d.]+) ms$")
ALIAS = re.compile(r"(?<=[(\[])([A-Za-z_]\w*)(?=[:)\]\s]|$)")
NODE_ARG = re.compile(r"\((\w+):(\w+)\)")
EDGE_ARG = re.compile(r"\[(\w+):(\w+)\]")


@dataclass
class Node:
    op: str
    arg: str | None
    depth: int
    records: int | None = None
    ms: float | None = None
    children: list[Node] = field(default_factory=list)


class Plan:
    def __init__(self, roots: list[Node]) -> None:
        self.roots = roots

    @property
    def root(self) -> Node:
        return self.roots[0]

    def walk(self):
        stack = list(reversed(self.roots))
        while stack:
            node = stack.pop()
            yield node
            stack.extend(reversed(node.children))

    def rows(self) -> list[tuple[int, str, str | None]]:
        return [(n.depth, n.op, n.arg) for n in self.walk()]

    def canonical(self) -> str:
        names: dict[str, str] = {}

        def rename(m):
            return names.setdefault(m.group(1), f"a{len(names)}")

        return "\n".join(f"{d}|{op}|{ALIAS.sub(rename, arg) if arg else ''}" for d, op, arg in self.rows())

    @property
    def sha(self) -> str:
        return hashlib.sha256(self.canonical().encode("utf-8")).hexdigest()

    def ops(self) -> list[str]:
        return [n.op for n in self.walk()]

    def index_scans(self) -> list[tuple[str, str]]:
        found = []
        for n in self.walk():
            if "Index Scan" in n.op and n.arg:
                edge = n.op.startswith("Edge")
                m = (EDGE_ARG if edge else NODE_ARG).search(n.arg)
                if m:
                    found.append(("edge" if edge else "node", m.group(2)))
        return found


def parse(lines: list[str]) -> Plan:
    roots: list[Node] = []
    stack: list[Node] = []
    for raw in lines:
        if not raw.strip():
            continue
        body = raw.lstrip(" ")
        depth = (len(raw) - len(body)) // 4
        parts = body.rstrip().split(" | ")
        records = ms = None
        if len(parts) > 1 and (m := METRICS.match(parts[-1])):
            records, ms = int(m.group(1)), float(m.group(2))
            parts = parts[:-1]
        node = Node(parts[0], " | ".join(parts[1:]) or None, depth, records, ms)
        del stack[depth:]
        (stack[-1].children if stack else roots).append(node)
        stack.append(node)
    return Plan(roots)
