"""gk: operator commands for the trust layer (master §12, §21.3).

    python -m gnomon.cli replay --graph t:<tenant> [--until S] [--target KEY]
    python -m gnomon.cli audit  --graph t:<tenant> [--checks K1,K4]
    python -m gnomon.cli why    --graph t:<tenant> --uid <entity uid>

Each prints one JSON document and exits 0 on success, 1 on divergence, a failed audit or an unknown entity.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

DEFAULT_URL = "localhost:6379"


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
    return parser


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
    db, client = _connect(args.url)
    try:
        code, out = _run(args, db, client)
    finally:
        client.close()
        db.connection.close()
    print(json.dumps(out, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
