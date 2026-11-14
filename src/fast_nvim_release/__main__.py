"""Allow ``python -m fast_nvim_release``."""

from __future__ import annotations

import sys

from fast_nvim_release.cli import main

if __name__ == "__main__":
    sys.exit(main())
