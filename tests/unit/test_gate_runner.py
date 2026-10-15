"""Gate runner (RUNBOOK_A §4.5, patch P1), TDD order checker and explain scaffold, run against throwaway git repos."""

import json
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.gate_h0]

ROOT = Path(__file__).resolve().parents[2]

FAKE_RUNBOOK = textwrap.dedent(
    """\
    # Fake runbook

    ### A00 · Fake bootstrap · gate H0

    ```text
    MODULE A00 · GATE H0 · DEPENDS: none

    SCOPE (create)
    src/app.py · README.md (title only)

    STEP 3 · EXIT GATE
    python scripts/gate.py H0

    HUMAN CHECKPOINT
    1 Why one? 2 Why two?
    3 Why three? 4 Why four? 5 Why five?
    ```

    ### A01 · Fake next · gate G0

    ```text
    MODULE A01 · GATE G0 · DEPENDS: H0

    SCOPE: src/next.py
    ```
    """
)

FAKE_PYPROJECT = textwrap.dedent(
    """\
    [tool.pytest.ini_options]
    testpaths = ["tests"]
    markers = ["unit: unit", "gate_h0: gate H0", "gate_g0: gate G0"]
    """
)

PASSING_TEST = "import pytest\n\npytestmark = pytest.mark.gate_h0\n\n\ndef test_ok():\n    assert 1 + 1 == 2\n"


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True, encoding="utf-8")
    return out.stdout


def _write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


@pytest.fixture
def fake_repo(tmp_path):
    """A repository that passes gate H0 except for the human signature."""
    repo = tmp_path / "app"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "Gate Test")
    _git(repo, "config", "user.email", "gate@test.invalid")
    _git(repo, "config", "core.autocrlf", "false")
    for name in ("gate.py", "check_tdd.py", "explain.py"):
        target = repo / "scripts" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / "scripts" / name, target)
    _write(repo, "docs/process/RUNBOOK_A_GNOMON_master.md", FAKE_RUNBOOK)
    _write(repo, "pyproject.toml", FAKE_PYPROJECT)
    _write(repo, "README.md", "# Fake\n")
    _commit(repo, "docs: fake runbook")

    _write(repo, "tests/test_app.py", PASSING_TEST)
    _write(repo, ".agent/evidence/A00/red-1.txt", "1 failed (ModuleNotFoundError: src.app)\n")
    _commit(repo, "test(A00): app batch")

    _write(repo, "src/app.py", "def answer():\n    return 42\n")
    _write(repo, ".agent/evidence/A00/green-1.txt", "1 passed\n")
    _commit(repo, "feat(A00): app batch")
    return repo


def _sign(repo: Path, signed: bool) -> None:
    _write(repo, "docs/explain/A00.md", f"# A00\n\n## Questions\n\n1 ok\n\nHUMAN-SIGNED: {'yes' if signed else 'no'}\n")


def _gate(repo: Path, *extra: str) -> tuple[int, dict]:
    proc = subprocess.run(
        [sys.executable, "scripts/gate.py", "H0", "--workers", "0", *extra],
        cwd=repo, capture_output=True, text=True, encoding="utf-8",
    )
    report_path = repo / "gates" / "H0.json"
    assert report_path.exists(), proc.stdout + proc.stderr
    return proc.returncode, json.loads(report_path.read_text(encoding="utf-8"))


def _check_tdd(repo: Path) -> tuple[int, dict]:
    proc = subprocess.run(
        [sys.executable, "scripts/check_tdd.py", "A00"],
        cwd=repo, capture_output=True, text=True, encoding="utf-8",
    )
    return proc.returncode, json.loads(proc.stdout)


def test_gate_green_on_clean_signed_repo(fake_repo):
    _sign(fake_repo, signed=True)
    rc, report = _gate(fake_repo)
    assert rc == 0
    assert report["status"] == "green"
    assert report["gate"] == "H0" and report["module"] == "A00"
    assert report["tests"]["passed"] == 1
    assert report["tests"]["failed"] == 0 and report["tests"]["skipped"] == 0
    assert report["forbidden_patterns"] == 0
    assert report["tdd"]["order_ok"] is True and report["tdd"]["batches"] == 1
    assert report["human_signed"] is True
    assert report["commit"] == _git(fake_repo, "rev-parse", "HEAD").strip()


def test_gate_fails_on_skip(fake_repo):
    _sign(fake_repo, signed=True)
    _write(fake_repo, "tests/test_skipped.py",
           "import pytest\n\npytestmark = pytest.mark.gate_h0\n\n\ndef test_later():\n    pytest.skip('later')\n")
    rc, report = _gate(fake_repo)
    assert rc != 0
    assert report["status"] == "red"
    assert report["tests"]["skipped"] == 1


def test_gate_fails_on_todo_in_scope(fake_repo):
    _sign(fake_repo, signed=True)
    _write(fake_repo, "src/app.py", "def answer():\n    # TODO compute it\n    return 42\n")
    rc, report = _gate(fake_repo)
    assert rc != 0
    assert report["status"] == "red"
    assert report["forbidden_patterns"] >= 1


def test_gate_fails_on_bare_pass_in_scope(fake_repo):
    _sign(fake_repo, signed=True)
    _write(fake_repo, "src/app.py", "def answer():\n    return 42\n\n\ndef later():\n    pass\n")
    rc, report = _gate(fake_repo)
    assert rc != 0
    assert report["forbidden_patterns"] == 1


def test_gate_ignores_patterns_outside_scope(fake_repo):
    _sign(fake_repo, signed=True)
    _write(fake_repo, "src/next.py", "def later():\n    pass  # TODO belongs to A01\n")
    rc, report = _gate(fake_repo)
    assert rc == 0
    assert report["forbidden_patterns"] == 0


def test_gate_requires_human_signed(fake_repo):
    rc, report = _gate(fake_repo)  # no explain doc at all
    assert rc != 0
    assert report["status"] == "red" and report["human_signed"] is False

    _sign(fake_repo, signed=False)
    rc, report = _gate(fake_repo)
    assert rc != 0
    assert report["status"] == "red" and report["human_signed"] is False


def test_gate_provisional_skips_only_human_signed(fake_repo):
    _sign(fake_repo, signed=False)
    rc, report = _gate(fake_repo, "--provisional")
    assert rc == 0
    assert report["status"] == "provisional"
    assert report["human_signed"] is False

    _write(fake_repo, "src/app.py", "def answer():\n    raise NotImplementedError\n")
    rc, report = _gate(fake_repo, "--provisional")
    assert rc != 0
    assert report["status"] == "red"


def test_check_tdd_requires_test_commit_before_feat(fake_repo):
    rc, report = _check_tdd(fake_repo)
    assert rc == 0
    assert report["order_ok"] is True
    assert report["batches"] == 1
    assert report["red_evidence"] == ["red-1.txt"]

    # A feat commit with no earlier, unpaired test commit breaks the order.
    _write(fake_repo, "src/app.py", "def answer():\n    return 43\n")
    _commit(fake_repo, "feat(A00): second batch without tests")
    rc, report = _check_tdd(fake_repo)
    assert rc != 0
    assert report["order_ok"] is False
    assert any("feat" in p for p in report["problems"])

    _sign(fake_repo, signed=True)
    rc, gate_report = _gate(fake_repo)
    assert rc != 0
    assert gate_report["tdd"]["order_ok"] is False


def test_check_tdd_requires_red_evidence_in_test_commit(tmp_path):
    repo = tmp_path / "app"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "Gate Test")
    _git(repo, "config", "user.email", "gate@test.invalid")
    target = repo / "scripts" / "check_tdd.py"
    target.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "scripts" / "check_tdd.py", target)
    _write(repo, "tests/test_app.py", PASSING_TEST)
    _commit(repo, "test(A00): app batch")  # no red-1.txt
    _write(repo, "src/app.py", "def answer():\n    return 42\n")
    _commit(repo, "feat(A00): app batch")
    rc, report = _check_tdd(repo)
    assert rc != 0
    assert any("red-1.txt" in p for p in report["problems"])


def test_explain_scaffold(fake_repo):
    proc = subprocess.run([sys.executable, "scripts/explain.py", "A00"], cwd=fake_repo,
                          capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 0, proc.stderr
    doc = (fake_repo / "docs" / "explain" / "A00.md").read_text(encoding="utf-8")
    for heading in ("## Built", "## Risky decisions", "## Questions"):
        assert heading in doc
    for question in ("Why one?", "Why two?", "Why three?", "Why four?", "Why five?"):
        assert question in doc
    assert doc.rstrip().endswith("HUMAN-SIGNED: no")

    again = subprocess.run([sys.executable, "scripts/explain.py", "A00"], cwd=fake_repo,
                           capture_output=True, text=True, encoding="utf-8")
    assert again.returncode != 0  # never overwrites an existing explain doc
