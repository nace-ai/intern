import sys
from pathlib import Path

# The package is `src/`, imported as `dates`: put a `dates` alias for it on the path.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "build" / "alias"))
