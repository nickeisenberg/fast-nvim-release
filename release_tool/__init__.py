"""Development tooling for nvim-bin: never part of the nvim-bin wheel.

    python -m release_tool --help

Run from the repository root.  ``src/`` goes on the import path here, so the
tooling can use the ``nvim_bin`` package (its platforms and ``build.json``)
without installing it.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPOROOT = Path(__file__).resolve().parent.parent

if str(REPOROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOROOT / "src"))
