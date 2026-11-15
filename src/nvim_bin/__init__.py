"""nvim-bin -- Neovim from PyPI.

A package version is the Neovim version it carries plus a revision of this
tool (``0.12.6.1`` is Neovim v0.12.6, revision 1):
``pip install "nvim-bin==0.12.*"`` gets the newest 0.12, and
``nvim-bin setup --prefix DIR`` installs it.
"""

from __future__ import annotations

from importlib import metadata

try:
    __version__ = metadata.version("nvim-bin")
except metadata.PackageNotFoundError:  # running from a source checkout
    __version__ = "0+unknown"

__all__ = ["__version__"]
