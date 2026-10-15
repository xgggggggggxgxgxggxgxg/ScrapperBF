"""Rend les modules racine et new_logic importables depuis les tests."""
import sys
from pathlib import Path

ROOT = Path(__file__).parent
for p in (ROOT, ROOT / "new_logic"):
    if p.is_dir() and str(p) not in sys.path:
        sys.path.insert(0, str(p))
