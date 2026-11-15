"""Allow ``python -m nvim_bin``."""

from __future__ import annotations

import sys

from nvim_bin.cli import main

if __name__ == "__main__":
    sys.exit(main())
