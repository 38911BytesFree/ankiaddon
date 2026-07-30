"""Make `core` importable on its own.

We put the *addon package directory* on sys.path rather than `src/`, so tests
import `core.gemini` directly and never touch `photo2cards/__init__.py` — that
file wires up Anki menus at import time and needs a running `aqt`.

That constraint is the point of the core/ vs ui/ split: if a test in here ever
starts needing Anki, logic has leaked into the wrong layer.
"""

import sys
from pathlib import Path

ADDON_DIR = Path(__file__).resolve().parent.parent / "src" / "photo2cards"
sys.path.insert(0, str(ADDON_DIR))
