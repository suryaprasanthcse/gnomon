#!/usr/bin/env bash
# Runs the executable proofs of master section 26 against a live FalkorDB (the docker-compose stack by default).
#
#   docker compose up -d --wait
#   scripts/demo_proofs.sh            # all twelve proofs
#   scripts/demo_proofs.sh P1 C G     # a selection
#
# Proof names: P1..P5 (Core) and A..G (Titan layers). GNOMON_FALKORDB_URL picks the server (default localhost:6379);
# GNOMON_PYTHON picks the interpreter (default: uv run python). Exits 0 only when every selected proof passed.
set -uo pipefail
cd "$(dirname "$0")/.."
export GNOMON_FALKORDB_URL="${GNOMON_FALKORDB_URL:-localhost:6379}"

target() {
  case "$1" in
    P1) echo "tests/e2e/test_proof_p1.py" ;;
    P2) echo "tests/e2e/test_proof_p2.py" ;;
    P3) echo "tests/e2e/test_proof_p3.py" ;;
    P4) echo "tests/e2e/test_proof_p4.py" ;;
    P5) echo "tests/e2e/test_proof_p5.py" ;;
    A) echo "tests/e2e/test_proof_a.py" ;;
    B) echo "tests/e2e/test_proof_b.py" ;;
    C) echo "tests/integration/test_a12_optimizer.py -k test_proof_c" ;;
    D) echo "tests/integration/test_a13_federation.py -k test_proof_d" ;;
    E) echo "tests/integration/executive_fed/test_federated_blast.py -k prefers_failover_to_isolation or subscription_mid_decision" ;;
    F) echo "tests/integration/temporal/test_asof_projections.py -k counterfactual" ;;
    G) echo "tests/e2e/test_proof_g.py" ;;
    *) return 1 ;;
  esac
}

proofs=("$@")
[ ${#proofs[@]} -eq 0 ] && proofs=(P1 P2 P3 P4 P5 A B C D E F G)
if [ -n "${GNOMON_PYTHON:-}" ]; then python_cmd=("$GNOMON_PYTHON"); else python_cmd=(uv run python); fi

failed=0
for proof in "${proofs[@]}"; do
  if ! spec="$(target "$proof")"; then
    echo "unknown proof '$proof' (known: P1 P2 P3 P4 P5 A B C D E F G)" >&2
    exit 2
  fi
  path="${spec%% *}"; rest="${spec#"$path"}"
  args=("$path")
  if [ -n "$rest" ]; then args+=(-k "${rest# -k }"); fi
  echo "== Proof $proof: ${spec}"
  if "${python_cmd[@]}" -m pytest "${args[@]}" -q --no-header -p no:randomly --timeout 1500; then
    echo "PROOF $proof: PASS"
  else
    echo "PROOF $proof: FAIL"
    failed=1
  fi
done
exit $failed
