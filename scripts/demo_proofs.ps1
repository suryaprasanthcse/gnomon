<#
Runs the executable proofs of master section 26 against a live FalkorDB (the docker-compose stack by default).

  docker compose up -d --wait
  scripts\demo_proofs.ps1            # all twelve proofs
  scripts\demo_proofs.ps1 P1 C G     # a selection

Proof names: P1..P5 (Core) and A..G (Titan layers). GNOMON_FALKORDB_URL picks the server (default localhost:6379);
GNOMON_PYTHON picks the interpreter (default: uv run python). Exits 0 only when every selected proof passed.
#>
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Proofs)

Set-Location (Join-Path $PSScriptRoot "..")
if (-not $env:GNOMON_FALKORDB_URL) { $env:GNOMON_FALKORDB_URL = "localhost:6379" }

$targets = [ordered]@{
    P1 = @("tests/e2e/test_proof_p1.py")
    P2 = @("tests/e2e/test_proof_p2.py")
    P3 = @("tests/e2e/test_proof_p3.py")
    P4 = @("tests/e2e/test_proof_p4.py")
    P5 = @("tests/e2e/test_proof_p5.py")
    A  = @("tests/e2e/test_proof_a.py")
    B  = @("tests/e2e/test_proof_b.py")
    C  = @("tests/integration/test_a12_optimizer.py", "-k", "test_proof_c")
    D  = @("tests/integration/test_a13_federation.py", "-k", "test_proof_d")
    E  = @("tests/integration/executive_fed/test_federated_blast.py", "-k", "prefers_failover_to_isolation or subscription_mid_decision")
    F  = @("tests/integration/temporal/test_asof_projections.py", "-k", "counterfactual")
    G  = @("tests/e2e/test_proof_g.py")
}

if (-not $Proofs -or $Proofs.Count -eq 0) { $Proofs = @($targets.Keys) }
if ($env:GNOMON_PYTHON) { $python = @($env:GNOMON_PYTHON) } else { $python = @("uv", "run", "python") }

$failed = 0
foreach ($proof in $Proofs) {
    if (-not $targets.Contains($proof)) {
        [Console]::Error.WriteLine("unknown proof '$proof' (known: $($targets.Keys -join ' '))")
        exit 2
    }
    Write-Output "== Proof ${proof}: $($targets[$proof] -join ' ')"
    $cmd = $python[0]
    $rest = @()
    if ($python.Count -gt 1) { $rest = $python[1..($python.Count - 1)] }
    & $cmd @rest -m pytest @($targets[$proof]) -q --no-header -p no:randomly --timeout 1500
    if ($LASTEXITCODE -eq 0) { Write-Output "PROOF ${proof}: PASS" } else { Write-Output "PROOF ${proof}: FAIL"; $failed = 1 }
}
exit $failed
