"""fast-nvim-release -- Neovim from PyPI.

A package version is the Neovim version it carries plus a revision of this
tool (``0.12.6.1`` is Neovim v0.12.6, revision 1):
``pip install "fast-nvim-release==0.12.*"`` gets the newest 0.12, and
``fast-nvim-release setup --prefix DIR`` installs it.
"""

from __future__ import annotations

from importlib import metadata

try:
    __version__ = metadata.version("fast-nvim-release")
except metadata.PackageNotFoundError:  # running from a source checkout
    __version__ = "0+unknown"

__all__ = ["__version__"]
