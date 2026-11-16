"""Build hook: version and tag the wheel from the Neovim build it carries.

``python -m release_tool fetch`` unpacks one Neovim build into
``src/nvim_bin/_nvim/`` and records it in ``_nvim/build.json``: the
package version (``<neovim>.<revision>``, e.g. ``0.12.6.1``) and the wheel's
platform tag.
This file reads both from there, so ``fetch`` then ``pip install .``
(or ``python -m build``) always produces a consistent wheel.
``python -m release_tool check`` verifies the result against ``lines.json``.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from setuptools import setup
from setuptools.command.bdist_wheel import bdist_wheel
from setuptools.command.sdist import sdist
from setuptools.dist import Distribution

BUILD_JSON = (
    Path(__file__).resolve().parent
    / "src"
    / "nvim_bin"
    / "_nvim"
    / "build.json"
)


def build_info() -> dict:
    try:
        return json.loads(BUILD_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


class BinaryDistribution(Distribution):
    """Not pure Python: keeps the package at the wheel root (platlib)."""

    def has_ext_modules(self):
        return True


class PlatformWheel(bdist_wheel):
    def get_tag(self):
        return "py3", "none", build_info().get("tag") or "unknown"

    def run(self):
        if not build_info():
            raise SystemExit(
                f"no Neovim build in {BUILD_JSON.parent}; run python -m release_tool fetch"
            )
        super().run()


class SourceDist(sdist):
    """An sdist never carries the vendored binaries, only the tracked files."""

    VENDORED = re.compile(
        r"^src/nvim_bin/_nvim/(?!LICENSE\.nvim-[^/]+\.txt$|\.gitkeep$)"
    )

    def make_release_tree(self, base_dir, files):
        kept = [
            name
            for name in files
            if not self.VENDORED.match(name.replace(os.sep, "/"))
        ]
        super().make_release_tree(base_dir, kept)


setup(
    # without a fetched build the version is a placeholder; building a wheel then stops
    version=build_info().get("version") or "0+unvendored",
    cmdclass={"bdist_wheel": PlatformWheel, "sdist": SourceDist},
    distclass=BinaryDistribution,
)
