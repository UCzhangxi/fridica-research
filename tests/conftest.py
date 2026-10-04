"""pytest runs with --import-mode=importlib (fridica's convention); make the helpers importable."""
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))


@pytest.fixture
def sock_dir():
    """A short-lived directory for a fake control socket (Unix socket paths are limited to ~100 bytes, so not tmp_path)."""
    d = tempfile.mkdtemp(prefix="fr-")
    yield d
    shutil.rmtree(d, ignore_errors=True)
