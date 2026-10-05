Test evidence for chengcli/fridica-research #24 at 4a8da252c875cab552c81a516e8fb2a0a5bade58
base 347268c519132d9b74cf64c66183b52543d994f4, 4 ahead / 0 behind main; Python 3.11.15 and 3.12.3 (ruff 0.16.10); 2026-10-05T01:00:23Z
CI (.github/workflows/ci.yml, ubuntu/macos x py3.11/3.12) runs: python -m pip install -e .[dev]; ruff check .; pytest -q. Run here on Linux only, fresh python3.11 and python3.12 venvs.
py3.11 `pip install -e .[dev]`: installed, no error.
py3.11 `ruff check .`: All checks passed!
py3.11 `pytest -q`: 199 passed in 10.95s
py3.12 `pip install -e .[dev]`: installed, no error.
py3.12 `ruff check .`: All checks passed!
py3.12 `pytest -q`: 199 passed in 11.26s
Full suite at head: 199 passed, 0 failed, 0 skipped, 0 errors (both Pythons).
Replay: README and CI name no replay command; the CLI has `replay`, run per the task example. `fridica-research replay tests/bootstrap`: 11/11 pass, exit 0. `fridica-research replay tests/bootstrap --strict`: 11/11 pass (strict), exit 0.
RED/GREEN: tests/ files the PR adds or changes: tests/test_github.py (added), tests/test_machine.py (modified), tests/bootstrap/000..010/config.json (11 modified). Copied all 13 onto a worktree of B, B installed in its own py3.12 venv.
RED tests/test_github.py on B: 1 error during collection, ImportError: cannot import name 'GitHub' from 'fridica_research.config'.
RED tests/test_machine.py on B: 1 failed, 49 passed; tests/test_machine.py::test_signoff_for_another_head_is_ignored_and_noted, AssertionError: assert ('Delivered' == 'Audit'.
RED full suite on B with copied tests: 1 error (collection of test_github.py, run interrupted); with --ignore=tests/test_github.py: 36 failed, 129 passed (35 in tests/test_replay.py: 24 TypeError: Config.__init__() got an unexpected keyword argument 'github', 11 AssertionError diffs on the "github" config block; plus the test_machine.py failure above).
GREEN at head (py3.12): tests/test_github.py 34 passed; tests/test_machine.py 50 passed; both together 84 passed.
Base suite at B (B's own tests, py3.12): 165 passed in 9.74s.
Steps that could not run: none. macOS matrix legs not reproduced (Linux container only).
