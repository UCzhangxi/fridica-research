Test evidence for chengcli/fridica-research #24 at 43888e80ffb8205cc08b97c4300fd3d69b0a79b9
base ccf6f2c44f6d6047a635f0c5d2a199879b085b58, 4 ahead / 0 behind main; Python 3.11.15 (no Rust in CI); 2026-10-05 18:32 UTC
CI (.github/workflows/ci.yml, on pull_request) runs, in a fresh python3 -m venv:
python -m pip install -e .[dev] -> installed OK (ruff 0.16.10)
ruff check . -> All checks passed!
pytest -q -> 202 passed in 11.06s
Full suite at head: 202 passed, 0 failed, 0 skipped, 0 errors.
Replay (cli.py `replay` subcommand, README names tests/test_bootstrap_tape.py):
fridica-research replay tests/bootstrap -> 11/11 pass (exit 0)
fridica-research replay tests/bootstrap --strict -> 11/11 pass (strict) (exit 0)
RED/GREEN: files the PR changes under tests/: tests/test_github.py, tests/test_machine.py and tests/bootstrap/000..010/config.json (11 fixtures). Copied all 13 over a worktree of B, separate venv with B installed editable.
RED on B source: tests/test_github.py -> collection ERROR, ImportError: cannot import name 'GitHub' from 'fridica_research.config'. tests/test_machine.py -> 1 failed, 50 passed; failure tests/test_machine.py::test_signoff_for_another_head_is_ignored_and_noted, AssertionError: assert ('Delivered' == 'Audit'). Full suite on B with copied tests (--continue-on-collection-errors) -> 36 failed, 132 passed, 1 error. Replay on B with copied fixtures (default and --strict) -> TypeError: Config.__init__() got an unexpected keyword argument 'github'.
GREEN at head: tests/test_github.py -> 34 passed; tests/test_machine.py -> 51 passed; together 85 passed.
Base suite at B, unmodified: ruff check . -> All checks passed!; pytest -q -> 168 passed in 10.27s; replay tests/bootstrap -> 11/11 pass; --strict -> 11/11 pass (strict).
Not run: the CI matrix's macOS and Python 3.12 legs (only Linux with Python 3.11.15 available here). Publishing: `git worktree add -B review-evidence` failed (fatal: 'review-evidence' is already used by worktree at '/home/user/evidence-pub-24-4a8da25'); committed from a detached worktree of origin/review-evidence instead.
