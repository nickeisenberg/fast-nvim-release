"""Build-time view of ``lines.json``: which wheels exist and what each carries.

``lines.json`` holds:

* ``revision``: one counter for this project's own code.  A wheel's version is
  the Neovim version it carries plus the revision: ``0.12.6.1`` is Neovim
  v0.12.6 with revision 1.  It goes up (``scripts/pins.py bump``) whenever the
  installed code changes, which re-releases every line's current patch.
* ``lines``: every supported Neovim line with

  * ``official``: the build from ``neovim/neovim`` (version, sha256 per
    platform, and platforms deliberately ``skip``-ped);
  * ``old_glibc`` (optional): the glibc 2.17 build from
    ``neovim/neovim-releases``, which can lag behind by a patch.  Its wheels
    then simply belong to the older version (``0.12.5.1``), and pip on an old
    system falls back to it.

Each (line, wheel tag) pair with a pinned build is one wheel.  A new Neovim
patch is a new version with the same revision; the old patch's releases stay
on the index as they are.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from fast_nvim_release.platforms import OLD_GLIBC_TAGS, PLATFORMS, Platform  # noqa: E402

LINES_FILE = ROOT / "lines.json"
#: where fetch_nvim.py unpacks a build, and where the tracked licenses live
NVIM_DIR = ROOT / "src" / "fast_nvim_release" / "_nvim"
REPOS = {"official": "neovim/neovim", "old_glibc": "neovim/neovim-releases"}


@dataclass(frozen=True)
class Build:
    kind: str  #: ``official`` or ``old_glibc``
    version: str  #: the Neovim release, e.g. ``v0.12.6``
    sha256: Dict[str, str]  #: platform key -> archive checksum

    @property
    def repo(self) -> str:
        return REPOS[self.kind]

    def url(self, platform_key: str) -> str:
        asset = PLATFORMS[platform_key].asset
        return f"https://github.com/{self.repo}/releases/download/{self.version}/{asset}"


@dataclass(frozen=True)
class Wheel:
    line: str  #: e.g. ``0.12``
    version: str  #: the package version, e.g. ``0.12.6.1``
    tag: str  #: the wheel's platform tag
    platform: Platform
    build: Build

    @property
    def label(self) -> str:
        return f"{self.version} {self.tag}"

    @property
    def filename(self) -> str:
        return f"fast_nvim_release-{self.version}-py3-none-{self.tag}.whl"


def line_key(line: str) -> Tuple[int, ...]:
    return tuple(int(part) for part in line.split("."))


def _ordered(data: dict) -> dict:
    specs = data["lines"]
    return {
        "revision": data["revision"],
        "lines": {k: specs[k] for k in sorted(specs, key=line_key)},
    }


def load(path: Path = LINES_FILE) -> dict:
    """``{"revision": int, "lines": {line: spec}}``, lines oldest first."""
    with path.open(encoding="utf-8") as handle:
        return _ordered(json.load(handle))


def save(data: dict, path: Path = LINES_FILE) -> None:
    path.write_text(json.dumps(_ordered(data), indent=2) + "\n", encoding="utf-8")


def license_name(line: str) -> str:
    """The license file each wheel of ``line`` carries, tracked in ``NVIM_DIR``."""
    return f"LICENSE.nvim-{line}.txt"


def license_url(line: str, data: dict) -> str:
    """Neovim's license at the tag of the line's official build."""
    tag = data["lines"][line]["official"]["version"]
    return f"https://raw.githubusercontent.com/neovim/neovim/{tag}/LICENSE.txt"


def newest_line(data: Optional[dict] = None) -> str:
    return list((data or load())["lines"])[-1]


def builds(spec: dict) -> List[Build]:
    return [
        Build(kind, spec[kind]["version"], dict(spec[kind]["sha256"]))
        for kind in ("official", "old_glibc")
        if spec.get(kind)
    ]


def package_version(build: Build, revision: int) -> str:
    """``v0.12.6`` with revision 1 -> ``0.12.6.1``."""
    return f"{build.version.lstrip('v')}.{revision}"


def wheels(data: Optional[dict] = None) -> List[Wheel]:
    """Every wheel ``lines.json`` describes, newest line last."""
    data = data or load()
    found = []
    for line, spec in data["lines"].items():
        for build in builds(spec):
            version = package_version(build, data["revision"])
            for key, platform in PLATFORMS.items():
                if key not in build.sha256:
                    continue
                tag = platform.wheel_tag if build.kind == "official" else OLD_GLIBC_TAGS[key]
                found.append(Wheel(line, version, tag, platform, build))
    return found


def find(line: str, tag: str, data: Optional[dict] = None) -> Wheel:
    """The wheel for ``line`` with platform ``tag``; ``KeyError`` saying what exists."""
    candidates = [wheel for wheel in wheels(data) if wheel.line == line]
    for wheel in candidates:
        if wheel.tag == tag:
            return wheel
    have = ", ".join(wheel.tag for wheel in candidates) or "none (unknown line)"
    raise KeyError(f"Neovim {line} has no build for {tag} (it has: {have})")
