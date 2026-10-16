"""Segment composition (master §6.1): one GRAPH.QUERY per proposal, kernel segments around the pinned body.

    R0 Genesis          PG · BODY · Q0 · RET      tick T0 on (0, 0), inside PG
    standard (P0)       P0 · BODY · Q0 · RET      tick T0, substituted for <TICK> in P0

    decision (P0)       P0 · P1 · BODY · Q0 · E0  P1 is spliced into P0 at its add-on mark, E0 returns the Decision

    federation (P2)     P0 · P2 · BODY · Q0 · RET  the tick is T2, Kernel.recv on the message's (s_pt, s_lc)
    federation (P3)     P0 · P3 · BODY · Q0 · RET  the tick is T3, Kernel.recv on the receipt's (r_pt, r_lc)

The add-ons P1/E0 (decisions, A10) and P2/P3 (federation, A13) are spliced into P0 at its add-on mark. This file,
the segments and the Kernel UDF library form `kernel_src`, whose sha Genesis pins as Head.kernel_sha.
"""

from __future__ import annotations

import hashlib
from functools import cache
from pathlib import Path

from gnomon.kernel.registry import RuleDef
from gnomon.udf import loader

SEGMENT_DIR = Path(__file__).resolve().parent / "segments"
INSTALLED = ("PG", "P0", "P1", "P2", "P3", "Q0", "E0", "RET")
SPLICED = ("P1", "P2", "P3")
T0 = "Kernel.tick(h.pt, h.lc, timestamp(), $mode, $rec_pt, $rec_lc)"
T2 = "Kernel.recv(h.pt, h.lc, timestamp(), $m.s_pt, $m.s_lc, $mode, $rec_pt, $rec_lc)"
T3 = "Kernel.recv(h.pt, h.lc, timestamp(), $m.r_pt, $m.r_lc, $mode, $rec_pt, $rec_lc)"
TICKS = {"P0": T0, "P2": T2, "P3": T3}
TICK = "<TICK>"
ADD_ON_MARK = "//-- add-on segment (P1 decision checks; P2/P3 federation) is inserted here --"


@cache
def segment(name: str) -> str:
    if name not in INSTALLED:
        raise ValueError(f"unknown segment {name!r}")
    return (SEGMENT_DIR / f"{name.lower()}.cypher").read_text(encoding="utf-8")


def plan(rule: RuleDef, sim: bool = False) -> list[str]:
    """Segment names in query order; "BODY" stands for the rule's own body.

    Sim mode (master §15.4) never emits P1 or E0: there is no decision yet and the branch is disposable."""
    if rule.prelude == "PG":
        if rule.name != "Genesis":
            raise ValueError(f"prelude PG is reserved for Genesis, not {rule.name!r}")
        return ["PG", "BODY", "Q0", "RET"]
    if rule.prelude not in ("P0", "P2", "P3"):
        raise ValueError(f"rule {rule.name!r} has unknown prelude {rule.prelude!r}")
    if sim and rule.prelude == "P0":
        return ["P0", "BODY", "Q0", "RET"]
    order = ["P0"]
    if rule.requires_decision:
        order.append("P1")
    if rule.prelude != "P0":
        order.append(rule.prelude)
    order += ["BODY", "Q0", "E0" if rule.requires_decision else "RET"]
    return order


def compose(rule: RuleDef, sim: bool = False) -> str:
    parts = []
    names = plan(rule, sim)
    for name in names:
        if name == "BODY":
            parts.append(rule.body)
        elif name in SPLICED:
            continue  # spliced into P0 below, between the tick and the apply variables
        else:
            text = segment(name)
            if name == "P0":
                spliced = [segment(n) for n in names if n in SPLICED]
                if spliced:
                    head, tail = text.split(ADD_ON_MARK)
                    text = head + "".join(spliced) + tail
                text = text.replace(TICK, TICKS[rule.prelude])
            parts.append(text)
    return "\n".join(part.rstrip("\n") for part in parts) + "\n"


def kernel_source() -> str:
    """Everything that decides how a rewrite is applied: the UDF library, this composer and its segments."""
    files = [
        ("udf/kernel.js", loader.source()),
        ("gnomon/kernel/composer.py", Path(__file__).read_text(encoding="utf-8")),
        *((f"gnomon/kernel/segments/{name.lower()}.cypher", segment(name)) for name in INSTALLED),
    ]
    return "".join(f"=== {name} ===\n{text}" for name, text in files)


def kernel_sha() -> str:
    return hashlib.sha256(kernel_source().encode("utf-8")).hexdigest()
