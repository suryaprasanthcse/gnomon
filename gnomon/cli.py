"""gk: operator commands for the trust layer (master §12, §21.3).

    python -m gnomon.cli replay --graph t:<tenant> [--until S] [--target KEY]
    python -m gnomon.cli audit  --graph t:<tenant> [--checks K1,K4]
    python -m gnomon.cli why    --graph t:<tenant> --uid <entity uid>
    python -m gnomon.cli run    --graph t:<tenant> [--seed 7] [--no-crash] [--record FILE | --replay FILE]
    python -m gnomon.cli proof  P1|P2|P3|P4|P5

Each prints one JSON document and exits 0 on success, 1 on divergence, a failed audit, an unknown entity, an
unresolved scenario or a failed proof. `run` bootstraps the tenant (it must be empty) and runs the Core scenario
(master §11.4); `--record` stores the model responses by prompt hash, `--replay` answers only from such a file.
`proof` runs the executable proof scenario for one Core row of §26 (the pytest module tests/e2e/test_proof_pN.py).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

DEFAULT_URL = "localhost:6379"
PROOFS = ("P1", "P2", "P3", "P4", "P5")


def _connect(url: str):
    import redis
    from falkordb import FalkorDB

    host, _, port = url.rpartition(":")
    return FalkorDB(host=host, port=int(port)), redis.Redis(host=host, port=int(port), decode_responses=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gk", description="GNOMON replay, audit and provenance")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("replay", "audit", "why"):
        sub = commands.add_parser(name)
        sub.add_argument("--url", default=os.environ.get("GNOMON_FALKORDB_URL", DEFAULT_URL))
        sub.add_argument("--graph", required=True)
        if name == "replay":
            sub.add_argument("--until", type=int)
            sub.add_argument("--target")
        elif name == "audit":
            sub.add_argument("--checks", help="comma-separated, e.g. K1,K4 (default: K1-K6)")
        else:
            sub.add_argument("--uid", required=True)
    run = commands.add_parser("run")
    run.add_argument("--url", default=os.environ.get("GNOMON_FALKORDB_URL", DEFAULT_URL))
    run.add_argument("--graph", required=True)
    run.add_argument("--seed", type=int, default=7)
    run.add_argument("--no-crash", action="store_true")
    mode = run.add_mutually_exclusive_group()
    mode.add_argument("--record", metavar="FILE")
    mode.add_argument("--replay", metavar="FILE")
    proof = commands.add_parser("proof")
    proof.add_argument("name", choices=PROOFS)
    return parser


def _run_core(args, db, client) -> tuple[int, dict]:
    from gnomon.agents.recorded import PolicyLLM, RecordedLLM, RecordingLLM
    from gnomon.sim.scenario import bootstrap_demo, run_core

    store: dict = {}
    if args.replay:
        llm = RecordedLLM.load(args.replay)
    else:
        llm = RecordingLLM(PolicyLLM(), store)
    result = run_core(bootstrap_demo(db, client, args.graph), seed=args.seed, llm=llm, crash=not args.no_crash)
    if args.record:
        RecordedLLM.dump(args.record, store, {"seed": args.seed, "crash": not args.no_crash,
                                              "state_hash": result.state_hash})
    return (0 if result.resolved else 1), {"graph": args.graph, "seed": args.seed, **result.summary()}


def _run_proof(args) -> tuple[int, dict]:
    import subprocess

    module = f"tests/e2e/test_proof_{args.name.lower()}.py"
    done = subprocess.run([sys.executable, "-m", "pytest", module, "-q", "--no-header", "-p", "no:randomly"],
                          capture_output=True, text=True)
    tail = [line for line in done.stdout.splitlines() if line.strip()][-1:]
    return (0 if done.returncode == 0 else 1), {"proof": args.name, "passed": done.returncode == 0, "pytest": tail}


def _run(args, db, client) -> tuple[int, dict]:
    from gnomon.audit.replay import Divergence, replay
    from gnomon.audit.runner import audit, why_entity

    if args.command == "replay":
        try:
            result = replay(db, client, args.graph, until=args.until, target=args.target)
        except Divergence as exc:
            return 1, {"divergence": exc.as_dict()}
        return 0, {"key": result.key, "seq": result.seq, "hash": result.hash}
    if args.command == "audit":
        checks = args.checks.split(",") if args.checks else None
        report = audit(db, client, args.graph, checks)
        return (0 if report.ok else 1), report.as_dict()
    found = why_entity(db.select_graph(args.graph), args.uid)
    return (0, found) if found else (1, {"error": f"no entity {args.uid!r} in {args.graph}"})


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "proof":
        code, out = _run_proof(args)
        print(json.dumps(out, indent=2, sort_keys=True))
        return code
    db, client = _connect(args.url)
    try:
        code, out = _run_core(args, db, client) if args.command == "run" else _run(args, db, client)
    finally:
        client.close()
        db.connection.close()
    print(json.dumps(out, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
