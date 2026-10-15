"""Segment composition (master §6.1): one GRAPH.QUERY per proposal, kernel segments around the pinned body.

    R0 Genesis          PG · BODY · Q0 · RET      tick T0 on (0, 0), inside PG
    standard (P0)       P0 · BODY · Q0 · RET      tick T0, substituted for <TICK> in P0

The add-ons P1/E0 (decisions, A10) and P2/P3 (federation, A13) are not installed yet; asking for one
raises SegmentNotInstalled. This file, the segments and the Kernel UDF library form `kernel_src`,
whose sha Genesis pins as Head.kernel_sha.
"""

from __future__ import annotations

import hashlib
from functools import cache
from pathlib import Path

from gnomon.kernel.registry import RuleDef
from gnomon.udf import loader

SEGMENT_DIR = Path(__file__).resolve().parent / "segments"
INSTALLED = ("PG", "P0", "Q0", "RET")
ADD_ONS = ("P1", "P2", "P3", "E0")
T0 = "Kernel.tick(h.pt, h.lc, timestamp(), $mode, $rec_pt, $rec_lc)"
TICK = "<TICK>"


class SegmentNotInstalled(LookupError):
    """A segment that a later module installs."""


@cache
def segment(name: str) -> str:
    if name in ADD_ONS:
        raise SegmentNotInstalled(f"segment not installed: {name}")
    if name not in INSTALLED:
        raise ValueError(f"unknown segment {name!r}")
    return (SEGMENT_DIR / f"{name.lower()}.cypher").read_text(encoding="utf-8")


def plan(rule: RuleDef) -> list[str]:
    """Segment names in query order; "BODY" stands for the rule's own body."""
    if rule.prelude == "PG":
        if rule.name != "Genesis":
            raise ValueError(f"prelude PG is reserved for Genesis, not {rule.name!r}")
        return ["PG", "BODY", "Q0", "RET"]
    if rule.prelude not in ("P0", "P2", "P3"):
        raise ValueError(f"rule {rule.name!r} has unknown prelude {rule.prelude!r}")
    order = ["P0"]
    if rule.requires_decision:
        order.append("P1")
    if rule.prelude != "P0":
        order.append(rule.prelude)
    order += ["BODY", "Q0", "E0" if rule.requires_decision else "RET"]
    return order


def compose(rule: RuleDef) -> str:
    parts = []
    for name in plan(rule):
        if name == "BODY":
            parts.append(rule.body)
        else:
            text = segment(name)
            parts.append(text.replace(TICK, T0) if name == "P0" else text)
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
