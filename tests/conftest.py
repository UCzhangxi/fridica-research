"""pytest runs with --import-mode=importlib (fridica's convention); make the helpers importable."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
