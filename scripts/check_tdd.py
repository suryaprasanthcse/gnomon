#!/usr/bin/env python3
"""TDD order check (RUNBOOK_A §4.5 step 4).

usage: python scripts/check_tdd.py <module>

Walks the history oldest-first. Every `feat(<module>)` commit must be paired with an earlier, still
unpaired `test(<module>)` commit that touched tests/, and the n-th pair's test commit must already
contain .agent/evidence/<module>/red-<n>.txt. Prints a JSON report; exits 1 when the order is broken.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import deque
from pathlib import Path

SEP = "\x1f"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8")


def check(module: str, root: Path) -> dict:
    problems: list[str] = []
    log = _git(root, "log", "--reverse", f"--format=%H{SEP}%s")
    if log.returncode != 0:
        problems.append(f"git log failed: {log.stderr.strip()}")
    test_subject = re.compile(rf"^test\({re.escape(module)}\):")
    feat_subject = re.compile(rf"^feat\({re.escape(module)}\):")

    pending: deque[str] = deque()
    pairs: list[tuple[str, str]] = []
    for line in log.stdout.splitlines():
        sha, subject = line.split(SEP, 1)
        if test_subject.match(subject):
            touched = _git(root, "show", "--name-only", "--format=", sha).stdout.splitlines()
            if any(path.startswith("tests/") for path in touched):
                pending.append(sha)
            else:
                problems.append(f"test commit {sha[:10]} touches no file under tests/")
        elif feat_subject.match(subject):
            if pending:
                pairs.append((pending.popleft(), sha))
            else:
                problems.append(f"feat commit {sha[:10]} ({subject!r}) has no earlier unpaired test({module}) commit")

    red_evidence = []
    for n, (test_sha, _) in enumerate(pairs, 1):
        name = f"red-{n}.txt"
        if _git(root, "cat-file", "-e", f"{test_sha}:.agent/evidence/{module}/{name}").returncode == 0:
            red_evidence.append(name)
        else:
            problems.append(f"{name} is missing from test commit {test_sha[:10]}")
    if not pairs:
        problems.append(f"no feat({module}) commit is paired with a test({module}) commit")

    return {
        "module": module,
        "batches": len(pairs),
        "pairs": [{"test": t, "feat": f} for t, f in pairs],
        "unpaired_tests": list(pending),
        "red_evidence": red_evidence,
        "order_ok": not problems,
        "problems": problems,
    }


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    report = check(args[0], Path(__file__).resolve().parents[1])
    print(json.dumps(report, indent=2))
    return 0 if report["order_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
