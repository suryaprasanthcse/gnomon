"""A17 · gate SHIP · a judge's first ten minutes: docker compose up, genesis, the Core scenario, the proof scripts.

The stack is the repository's own docker-compose.yml, started under a throw-away project name on a free port, so a
developer's own stack on 6379 is never touched. Everything after `up` talks to it through GNOMON_FALKORDB_URL.
"""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
import redis
import yaml

pytestmark = [pytest.mark.e2e, pytest.mark.gate_ship]

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "docker-compose.yml"
PROOF_NAMES = ["P1", "P2", "P3", "P4", "P5", "A", "B", "C", "D", "E", "F", "G"]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def compose(project: str, port: int, *args: str, timeout: int = 300) -> subprocess.CompletedProcess:
    env = {**os.environ, "GNOMON_PORT": str(port)}
    return subprocess.run(["docker", "compose", "-p", project, "-f", str(COMPOSE), *args], cwd=ROOT, env=env,
                          capture_output=True, text=True, encoding="utf-8", timeout=timeout)


@pytest.fixture(scope="module")
def stack():
    if shutil.which("docker") is None:
        pytest.fail("docker is required: the clean-install test starts the compose stack")
    project, port = f"gnomon-ship-{uuid.uuid4().hex[:8]}", free_port()
    up = compose(project, port, "up", "-d", "--wait", "--wait-timeout", "120")
    try:
        assert up.returncode == 0, up.stderr
        yield {"project": project, "port": port, "url": f"127.0.0.1:{port}"}
    finally:
        compose(project, port, "down", "-v", "--remove-orphans")


def test_compose_file_pins_the_image_and_fsyncs_every_commit():
    spec = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    service = spec["services"]["falkordb"]
    state = (ROOT / ".agent" / "STATE.md").read_text(encoding="utf-8")
    digest = re.search(r"^falkordb_image:\s*(\S+)", state, re.M).group(1)
    assert service["image"] == digest and "@sha256:" in digest
    assert "--appendonly yes" in service["environment"]["REDIS_ARGS"] and "appendfsync always" in service["environment"]["REDIS_ARGS"]
    assert "healthcheck" in service and any("GNOMON_PORT" in str(p) for p in service["ports"])
    assert set(spec["services"]) == {"falkordb"}, "FalkorDB is the only service (rules 2 and 3)"


def test_clean_install_genesis_then_the_core_scenario_is_green(stack, tmp_path):
    client = redis.Redis(host="127.0.0.1", port=stack["port"], decode_responses=True)
    assert client.ping() and client.execute_command("GRAPH.LIST") == []  # nothing preloaded: a clean checkout
    graph = f"t:ship-{uuid.uuid4().hex[:6]}"
    run = subprocess.run([sys.executable, "-m", "gnomon.cli", "run", "--url", stack["url"], "--graph", graph, "--seed", "7"],
                         cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=600)
    assert run.returncode == 0, run.stdout + run.stderr
    summary = json.loads(run.stdout)
    assert summary["resolved"] is True and summary["graph"] == graph
    audit = subprocess.run([sys.executable, "-m", "gnomon.cli", "audit", "--url", stack["url"], "--graph", graph],
                           cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=600)
    assert audit.returncode == 0, audit.stdout + audit.stderr
    report = json.loads(audit.stdout)
    assert report["ok"] is True and report["failed"] == []
    client.close()


def gk(stack, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "gnomon.cli", args[0], "--url", stack["url"], *args[1:]], cwd=ROOT,
                          capture_output=True, text=True, encoding="utf-8", timeout=600)


def test_restart_keeps_the_log_because_aof_fsyncs_every_commit(stack):
    graph = f"t:ship-{uuid.uuid4().hex[:6]}"
    run = gk(stack, "run", "--graph", graph, "--seed", "7", "--no-crash")
    assert run.returncode == 0, run.stdout + run.stderr
    first = gk(stack, "replay", "--graph", graph)
    assert first.returncode == 0, first.stdout + first.stderr
    before = json.loads(first.stdout)
    assert compose(stack["project"], stack["port"], "restart", "falkordb").returncode == 0
    deadline = time.monotonic() + 90
    client = redis.Redis(host="127.0.0.1", port=stack["port"], decode_responses=True)
    while True:
        try:
            if client.ping():
                break
        except redis.RedisError:
            if time.monotonic() > deadline:
                raise
            time.sleep(1)
    client.close()
    again = gk(stack, "replay", "--graph", graph)
    assert again.returncode == 0, again.stdout + again.stderr
    assert json.loads(again.stdout) == before and before["seq"] > 0
    assert gk(stack, "audit", "--graph", graph).returncode == 0


def script_command(proof: str) -> list[str]:
    if sys.platform == "win32":
        return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "scripts" / "demo_proofs.ps1"), proof]
    return ["bash", str(ROOT / "scripts" / "demo_proofs.sh"), proof]


@pytest.mark.parametrize("proof", PROOF_NAMES)
def test_demo_scripts_run(stack, proof):
    env = {**os.environ, "GNOMON_FALKORDB_URL": stack["url"], "GNOMON_PYTHON": sys.executable}
    done = subprocess.run(script_command(proof), cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8",
                          timeout=1500)
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-2000:]
    assert f"PROOF {proof}: PASS" in done.stdout


def test_both_script_flavours_name_every_proof_and_fail_on_an_unknown_one():
    shell = (ROOT / "scripts" / "demo_proofs.sh").read_text(encoding="utf-8")
    power = (ROOT / "scripts" / "demo_proofs.ps1").read_text(encoding="utf-8")
    for proof in PROOF_NAMES:
        assert re.search(rf"\b{proof}\b", shell) and re.search(rf"\b{proof}\b", power)
    bad = subprocess.run(script_command("Z9"), cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert bad.returncode != 0 and "unknown proof" in (bad.stdout + bad.stderr).lower()
