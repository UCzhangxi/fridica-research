Test evidence for chengcli/fridica-research #23 at 3cf0038e307707d27d7c7cacd346cf9fed04aa52
base 347268c519132d9b74cf64c66183b52543d994f4, 8 ahead / 0 behind main; Python 3.11.15 and 3.12.3 (Linux, CI matrix ubuntu/macos x py3.11/3.12, macOS not run); 2026-10-05 00:43 UTC
Fetched pull/23/head = 3cf0038e307707d27d7c7cacd346cf9fed04aa52. CI (.github/workflows/ci.yml) runs: pip install -e .[dev], ruff check ., pytest -q. Each run in a fresh python3 -m venv.
py3.11: python -m pip install -e .[dev] -> exit 0
py3.11: ruff check . -> All checks passed!
py3.11: pytest -q -> 329 passed in 17.19s
py3.12: python -m pip install -e .[dev] -> exit 0
py3.12: ruff check . -> All checks passed!
py3.12: pytest -q -> 329 passed in 17.30s
Full suite at head: 329 passed, 0 failed, 0 skipped, 0 errors (both Pythons). No failures.
Replay (named in docs/protocol.md): fridica-research replay tests/bootstrap -> 11/11 pass (exit 0)
fridica-research replay tests/bootstrap --strict -> 11/11 pass (strict) (exit 0)
Test files changed under tests/ (B..head): tests/test_bootstrap_backend.py (added), tests/test_driver.py, tests/test_physics.py, and tests/bootstrap/000..010/config.json (11 corpus configs).
RED (B worktree, B source, head test files copied over, py3.11): pytest -q tests/test_bootstrap_backend.py tests/test_driver.py tests/test_physics.py -> 3 errors in 0.37s (collection errors, 0 passed, 0 failed); last line in each file: E   ModuleNotFoundError: No module named 'fridica_research.backend'
RED full suite at B with copied tests: 3 errors in 0.20s (collection interrupted)
RED replay at B with copied corpus: fridica-research replay tests/bootstrap and --strict both end: TypeError: Config.__init__() got an unexpected keyword argument 'backend'
GREEN (head, py3.11): same three files -> 186 passed in 15.48s
Base suite at B (unmodified, py3.11): ruff check . -> All checks passed!; pytest -q -> 165 passed in 10.00s
Steps not run: the macos-latest CI legs (no macOS host available). All other steps ran.
