#!/usr/bin/env python3
"""Regenerate tests/fixtures/llm/core.json: the Core scenario (seed 7, one crash) recorded from the PolicyLLM.

usage: python scripts/record_core_fixture.py

Starts a Testcontainers FalkorDB, bootstraps a test tenant exactly as the test harness does, runs the scenario
with every response recorded, and writes the responses plus the final state hash the replay must reproduce.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TARGET = ROOT / "tests" / "fixtures" / "llm" / "core.json"
SEED = 7


def main() -> int:
    import redis
    from falkordb import FalkorDB

    from gnomon.agents.recorded import PolicyLLM, RecordedLLM, RecordingLLM
    from gnomon.kernel.service import KernelHandle
    from gnomon.sim.scenario import run_core
    from tests.conftest import FalkorContainer, bootstrap_test_tenant, kernel_test_registry

    container = FalkorContainer()
    endpoint = container.start()
    try:
        client = redis.Redis(host=endpoint.host, port=endpoint.port, decode_responses=True)
        db = FalkorDB(host=endpoint.host, port=endpoint.port)
        key = f"t:test-{uuid.uuid4().hex[:8]}"
        outcome = bootstrap_test_tenant(db, client, key)
        if outcome.code != "APPLIED":
            raise SystemExit(f"genesis failed: {outcome}")
        store: dict = {}
        result = run_core(KernelHandle(db.select_graph(key), kernel_test_registry()), seed=SEED,
                          llm=RecordingLLM(PolicyLLM(), store), crash=True)
        if not result.resolved:
            raise SystemExit("the scenario did not resolve the incident")
        meta = {"seed": SEED, "crash": True, "state_hash": result.state_hash, "prompts": len(store),
                "recorded_from": "gnomon.agents.recorded.PolicyLLM (deterministic stand-in, not a language model)"}
        RecordedLLM.dump(TARGET, store, meta)
        print(f"wrote {TARGET.relative_to(ROOT)}: {len(store)} responses, state_hash {result.state_hash}")
        return 0
    finally:
        container.stop()


if __name__ == "__main__":
    sys.exit(main())
