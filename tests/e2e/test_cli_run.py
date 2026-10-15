"""gk run and gk proof (master §11.4, §26)."""

import json

import pytest

from gnomon import cli
from tests.support.sim_world import new_key

pytestmark = [pytest.mark.e2e, pytest.mark.gate_g4b]


def run(capsys, url, *extra):
    key = new_key()
    code = cli.main(["run", "--url", url, "--graph", key, "--seed", "7", *extra])
    return code, json.loads(capsys.readouterr().out), key


def test_gk_run_is_deterministic_and_replays_a_recording(falkordb_container, redis_raw, capsys, tmp_path):
    url = falkordb_container.url
    path = tmp_path / "rec.json"
    keys = []
    try:
        code1, first, k1 = run(capsys, url, "--record", str(path))
        code2, second, k2 = run(capsys, url, "--replay", str(path))
        keys = [k1, k2]
        assert code1 == code2 == 0 and first["resolved"] and first["state_hash"] == second["state_hash"]
        assert second["llm_misses"] == 0 and first["llm_misses"] == first["llm_calls"] > 0
    finally:
        for key in keys:
            redis_raw.execute_command("GRAPH.DELETE", key)


def test_gk_proof_accepts_only_the_core_proofs(capsys):
    for bad in ("P6", "A"):
        with pytest.raises(SystemExit):
            cli.main(["proof", bad])
    capsys.readouterr()
    assert cli.PROOFS == ("P1", "P2", "P3", "P4", "P5")
