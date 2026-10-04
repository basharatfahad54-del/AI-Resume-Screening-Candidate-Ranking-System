"""Pytest bootstrap: make ``ml/src`` importable without installing the package.

Keeps ``python -m pytest ml`` working straight from a checkout, which is how CI
runs it. ``pip install -e ml`` also works and takes precedence if present.
"""

from __future__ import annotations

import sys
from pathlib import Path

ML_SRC = Path(__file__).resolve().parent / "src"
if str(ML_SRC) not in sys.path:
    sys.path.insert(0, str(ML_SRC))