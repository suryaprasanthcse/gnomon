#!/usr/bin/env python3
"""Scaffold docs/explain/<module>.md, the rule-12 explain-back for a module.

usage: python scripts/explain.py <module> [--force]

Sections: Built · Risky decisions · Questions (the module's HUMAN CHECKPOINT, five slots) ·
HUMAN-SIGNED: no. Only the human sets the last line to `HUMAN-SIGNED: yes`, after answering.
"""

from __future__ import annotations

import argparse
import re
import sys

from gate import ROOT, _strip_parentheticals, load_modules

QUESTION = re.compile(r"(?:^|(?<=\s))\d\s+(.*?)(?=\s\d\s+[A-Z]|$)")
SLOTS = 5


def checkpoint_questions(text: str) -> list[str]:
    flat = " ".join(_strip_parentheticals(text).split())
    questions = [q.strip() for q in QUESTION.findall(flat) if q.strip()]
    return (questions + [""] * SLOTS)[:max(SLOTS, len(questions))]


def render(module_id: str, gate: str, questions: list[str]) -> str:
    lines = [
        f"# {module_id} · gate {gate} · explain-back",
        "",
        "## Built",
        "",
        "(Written by the agent: what this module built and where it lives.)",
        "",
        "## Risky decisions",
        "",
        "(Written by the agent: the three riskiest decisions and why they were taken.)",
        "",
        "## Questions",
        "",
        "Answer each question in your own words (rule 12). Then set the last line to `HUMAN-SIGNED: yes`.",
        "",
    ]
    for n, question in enumerate(questions, 1):
        lines += [f"### Q{n}. {question}".rstrip(), "", "Answer:", ""]
    lines.append("HUMAN-SIGNED: no")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="scaffold docs/explain/<module>.md")
    parser.add_argument("module")
    parser.add_argument("--force", action="store_true", help="overwrite an existing explain doc")
    args = parser.parse_args(argv)
    by_id = {m.id: m for m in load_modules().values()}
    if args.module not in by_id:
        print(f"unknown module {args.module!r}", file=sys.stderr)
        return 2
    module = by_id[args.module]
    target = ROOT / "docs" / "explain" / f"{module.id}.md"
    if target.exists() and not args.force:
        print(f"{target.relative_to(ROOT).as_posix()} already exists; refusing to overwrite", file=sys.stderr)
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    questions = checkpoint_questions(module.sections.get("HUMAN CHECKPOINT", ""))
    target.write_text(render(module.id, module.gate, questions), encoding="utf-8", newline="\n")
    print(target.relative_to(ROOT).as_posix())
    return 0


if __name__ == "__main__":
    sys.exit(main())
