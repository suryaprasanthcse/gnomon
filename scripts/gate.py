#!/usr/bin/env python3
"""Cumulative gate runner (RUNBOOK_A §4.5, patch P1).

usage: python scripts/gate.py <GATE> [--provisional] [--replay] [--workers N]

Modules, gates, the gate DAG and each module's SCOPE are read from the runbook in
docs/process/, so later modules never edit this file. Checks, in order:
  1 pytest over every test marked with this gate or any earlier gate in the DAG (-n N, junit evidence)
  2 no skipped or xfailed test in the gate set, and at least one test selected
  3 no forbidden pattern in the module's SCOPE files
  4 scripts/check_tdd.py <module>: every feat commit follows a test commit with red evidence
  5 coverage of the gnomon packages in SCOPE (kernel and audit >= 90%, others >= 80%)
  6 docs/explain/<module>.md carries HUMAN-SIGNED: yes (skipped with --provisional)
Writes gates/<GATE>.json with status green | provisional | red; exits non-zero when red.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import check_tdd

ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = ROOT / "docs" / "process" / "RUNBOOK_A_GNOMON_master.md"
STATE = ROOT / ".agent" / "STATE.md"

MODULE_HEADER = re.compile(r"^MODULE\s+(A\d{2})\s+·\s+GATE\s+(\S+)\s+·\s+DEPENDS:\s*(.*)$")
SECTION_LABEL = re.compile(
    r"^(CAPSULE|READ FIRST|PRECONDITIONS|SPEC INPUTS|SCOPE|STEP \d|HUMAN CHECKPOINT|STOP CONDITIONS|STOP|OUT OF SCOPE|ORRERY)\b"
)
FILENAME_TOKEN = re.compile(r"[\w.-]+\.[A-Za-z0-9]+")
COVERAGE_FLOOR = {"gnomon/kernel": 0.90, "gnomon/audit": 0.90}
DEFAULT_COVERAGE_FLOOR = 0.80

# Assembled from pieces so this file does not match its own scan.
FORBIDDEN_TEXT = {
    "todo marker": re.compile(r"\bTO" + r"DO\b"),
    "not-implemented error": re.compile(r"\bNotImplemented" + r"Error\b"),
    "pytest skip": re.compile(r"\bpytest\.sk" + r"ip\b"),
    "expected-failure marker": re.compile(r"\bxf" + r"ail\b"),
}


@dataclass
class Module:
    id: str
    gate: str
    depends: list[str]
    sections: dict[str, str] = field(default_factory=dict)

    @property
    def scope(self) -> list[str]:
        return parse_scope(self.sections.get("SCOPE", ""))


# ---------------------------------------------------------------- runbook parsing

def _strip_parentheticals(text: str) -> str:
    previous = None
    while previous != text:
        previous, text = text, re.sub(r"\([^()]*\)", " ", text)
    return text


def _expand_braces(token: str) -> list[str]:
    match = re.search(r"\{([^{}]*)\}", token)
    if not match:
        return [token]
    head, tail = token[: match.start()], token[match.end():]
    return [out for alt in match.group(1).split(",") for out in _expand_braces(head + alt + tail)]


def _normalize_pattern(token: str) -> str:
    segments = []
    for segment in token.split("/"):
        if "…" in segment:  # an elided range such as en_r4…en_r11.cypher becomes *.cypher
            last = segment.split("…")[-1]
            segment = "*" + (Path(last).suffix if "." in last else "")
        segments.append(segment)
    pattern = "/".join(segments)
    return pattern + "**" if pattern.endswith("/") else pattern


def parse_scope(text: str) -> list[str]:
    flat = " ".join(text.split())
    flat = re.sub(r"\{[^{}]*\}", lambda m: re.sub(r"\s+", "", m.group(0)), flat)
    flat = _strip_parentheticals(flat)
    patterns: list[str] = []
    directory = ""
    for token in re.split(r"[\s·]+", flat):
        token = token.strip(",;:")
        if not token or not ("/" in token or FILENAME_TOKEN.fullmatch(token)):
            continue
        # A bare file name ("noop.yaml + noop.cypher") may belong to the previous path's directory.
        candidates = [token] if "/" in token or not directory else [token, directory + token]
        if "/" in token:
            directory = token.rsplit("/", 1)[0] + "/"
        for candidate in candidates:
            for expanded in _expand_braces(candidate):
                pattern = _normalize_pattern(expanded)
                if pattern not in patterns:
                    patterns.append(pattern)
    return patterns


def _fenced_blocks(lines: list[str]) -> list[list[str]]:
    blocks, current = [], None
    for line in lines:
        if current is None and line.strip() == "```text":
            current = []
        elif current is not None and line.strip() == "```":
            blocks.append(current)
            current = None
        elif current is not None:
            current.append(line)
    return blocks


def load_modules(runbook: Path = RUNBOOK) -> dict[str, Module]:
    """Every runbook module block, keyed by gate id."""
    modules: dict[str, Module] = {}
    for block in _fenced_blocks(runbook.read_text(encoding="utf-8").splitlines()):
        body = [line for line in block if line.strip()]
        header = MODULE_HEADER.match(body[0].strip()) if body else None
        if not header:
            continue
        depends = [d for d in re.split(r"[,\s]+", _strip_parentheticals(header.group(3))) if d and d != "none"]
        module = Module(id=header.group(1), gate=header.group(2), depends=depends)
        label, buffer = None, []
        for line in block[1:]:
            found = SECTION_LABEL.match(line)
            if found:
                if label:
                    module.sections[label] = "\n".join(buffer)
                label = found.group(1)
                buffer = [line[found.end():].lstrip(" :")]
            elif label:
                buffer.append(line)
        if label:
            module.sections[label] = "\n".join(buffer)
        modules[module.gate] = module
    for module in modules.values():
        unknown = [d for d in module.depends if d not in modules]
        if unknown:
            raise ValueError(f"{module.id} depends on unknown gate(s) {unknown}")
    return modules


def cumulative_gates(gate: str, modules: dict[str, Module]) -> list[str]:
    """The gate plus every gate it transitively depends on, in runbook order."""
    seen: set[str] = set()
    stack = [gate]
    while stack:
        current = stack.pop()
        if current not in seen:
            seen.add(current)
            stack.extend(modules[current].depends)
    return [g for g in modules if g in seen]


def marker(gate: str) -> str:
    return "gate_" + gate.lower()


# ---------------------------------------------------------------- checks

def scope_files(patterns: list[str]) -> list[Path]:
    found = {path for pattern in patterns for path in ROOT.glob(pattern) if path.is_file()}
    return sorted(found)


def _bare_pass_lines(tree: ast.AST) -> list[int]:
    hits = []
    for node in ast.walk(tree):
        for attr in ("body", "orelse", "finalbody"):
            body = getattr(node, attr, None)
            if isinstance(body, list) and len(body) == 1 and isinstance(body[0], ast.Pass):
                hits.append(body[0].lineno)
    return sorted(hits)


def forbidden_hits(files: list[Path]) -> list[str]:
    hits = []
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for name, pattern in FORBIDDEN_TEXT.items():
                if pattern.search(line):
                    hits.append(f"{rel}:{lineno}: {name}")
        if path.suffix == ".py":
            try:
                tree = ast.parse(text, filename=rel)
            except SyntaxError as exc:
                hits.append(f"{rel}:{exc.lineno}: unparseable Python")
                continue
            hits.extend(f"{rel}:{lineno}: bare pass body" for lineno in _bare_pass_lines(tree))
    return hits


def coverage_packages(patterns: list[str]) -> list[str]:
    packages = []
    for pattern in patterns:
        parts = pattern.split("/")
        if len(parts) >= 3 and parts[0] == "gnomon" and "*" not in parts[1]:
            package = f"gnomon/{parts[1]}"
            if package not in packages and any((ROOT / package).rglob("*.py")):
                packages.append(package)
    return packages


def coverage_ratios(report: Path, packages: list[str]) -> dict[str, float]:
    files = json.loads(report.read_text(encoding="utf-8")).get("files", {})
    ratios = {}
    for package in packages:
        covered = total = 0
        for name, data in files.items():
            if name.replace("\\", "/").startswith(package + "/"):
                summary = data["summary"]
                covered += summary["covered_lines"] + summary.get("covered_branches", 0)
                total += summary["num_statements"] + summary.get("num_branches", 0)
        ratios[package] = round(covered / total, 4) if total else 0.0
    return ratios


def junit_counts(path: Path) -> dict:
    counts = {"passed": 0, "failed": 0, "skipped": 0, "duration_s": 0.0}
    if not path.exists():
        return counts
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
    counts["duration_s"] = round(sum(float(s.get("time", 0)) for s in suites), 3)
    for case in root.iter("testcase"):
        if case.find("failure") is not None or case.find("error") is not None:
            counts["failed"] += 1
        elif case.find("skipped") is not None:
            counts["skipped"] += 1
        else:
            counts["passed"] += 1
    return counts


def human_signed(module: str) -> bool:
    doc = ROOT / "docs" / "explain" / f"{module}.md"
    return doc.exists() and re.search(r"^HUMAN-SIGNED:\s*yes\s*$", doc.read_text(encoding="utf-8"), re.M | re.I) is not None


def image_digest() -> str:
    if STATE.exists():
        found = re.search(r"^falkordb_image:\s*\S+@(sha256:[0-9a-f]{64})", STATE.read_text(encoding="utf-8"), re.M)
        if found:
            return found.group(1)
    return "unpinned"


def head_commit() -> str:
    out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    return out.stdout.strip() if out.returncode == 0 else ""


# ---------------------------------------------------------------- run

def run_gate(gate: str, provisional: bool, workers: int, replay: bool = False) -> dict:
    modules = load_modules()
    if gate not in modules:
        raise SystemExit(f"unknown gate {gate!r}; known gates: {', '.join(modules)}")
    module = modules[gate]
    gates = cumulative_gates(gate, modules)
    evidence = ROOT / ".agent" / "evidence" / gate
    evidence.mkdir(parents=True, exist_ok=True)
    junit, cov_json = evidence / "junit.xml", evidence / "coverage.json"
    junit.unlink(missing_ok=True)
    cov_json.unlink(missing_ok=True)
    failures: list[str] = []

    patterns = module.scope
    packages = coverage_packages(patterns)
    cmd = [sys.executable, "-m", "pytest", "-m", " or ".join(marker(g) for g in gates),
           "--strict-markers", "-p", "no:randomly", f"--junitxml={junit}"]
    if workers > 0:
        cmd += ["-n", str(workers)]
    if packages:
        cmd += [f"--cov={p}" for p in packages] + [f"--cov-report=json:{cov_json}", "--cov-report="]
    print("$ " + " ".join(cmd), flush=True)
    exit_code = subprocess.run(cmd, cwd=ROOT).returncode

    tests = junit_counts(junit)
    if exit_code == 5 or tests["passed"] + tests["failed"] + tests["skipped"] == 0:
        failures.append("no tests selected for the gate set")
    elif exit_code != 0:
        failures.append(f"pytest exited with {exit_code} ({tests['failed']} failed or errored)")
    if tests["skipped"]:
        failures.append(f"{tests['skipped']} skipped or xfailed test(s) in the gate set")

    hits = forbidden_hits(scope_files(patterns))
    if hits:
        failures.append(f"{len(hits)} forbidden pattern(s) in scope")

    tdd = check_tdd.check(module.id, ROOT)
    if not tdd["order_ok"]:
        failures.append("TDD order check failed: " + "; ".join(tdd["problems"]))

    coverage = coverage_ratios(cov_json, packages) if packages and cov_json.exists() else {}
    coverage_warnings: list[str] = []
    for package in packages:
        floor = COVERAGE_FLOOR.get(package, DEFAULT_COVERAGE_FLOOR)
        if coverage.get(package, 0.0) < floor:
            shortfall = f"coverage {package} {coverage.get(package, 0.0):.2%} < {floor:.0%}"
            # A replay runs an early gate's tests against today's source, which later modules have grown,
            # so the floor met when the gate was authored cannot be met again; report it, do not fail it.
            (coverage_warnings if replay else failures).append(shortfall)

    signed = human_signed(module.id)
    if failures:
        status = "red"
    elif signed:
        status = "green"
    elif provisional:
        status = "provisional"
    else:
        status = "red"
        failures.append(f"docs/explain/{module.id}.md is not HUMAN-SIGNED: yes")

    report = {
        "gate": gate,
        "module": module.id,
        "status": status,
        "commit": head_commit(),
        "utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "image_digest": image_digest(),
        "markers": [marker(g) for g in gates],
        "tests": tests,
        "coverage": coverage,
        "tdd": {"batches": tdd["batches"], "red_evidence": tdd["red_evidence"], "order_ok": tdd["order_ok"]},
        "forbidden_patterns": len(hits),
        "human_signed": signed,
        "failures": failures,
        "coverage_warnings": coverage_warnings,
        "forbidden": hits,
    }
    out = ROOT / "gates" / f"{gate}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("gate")
    parser.add_argument("--provisional", action="store_true", help="skip only the HUMAN-SIGNED check (patch P1)")
    parser.add_argument("--workers", type=int, default=4, help="pytest-xdist workers (0 = single process)")
    parser.add_argument("--replay", action="store_true", help="CI re-run of a recorded gate: coverage floors warn instead of fail")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(errors="backslashreplace")
    report = run_gate(args.gate, args.provisional, args.workers, args.replay)
    print(f"gate {report['gate']} ({report['module']}): {report['status']}")
    for failure in report["failures"]:
        print(f"  - {failure}")
    for warning in report["coverage_warnings"]:
        print(f"  ~ {warning} (replay: not enforced)")
    for hit in report["forbidden"]:
        print(f"    {hit}")
    return 0 if report["status"] in ("green", "provisional") else 1


if __name__ == "__main__":
    sys.exit(main())
