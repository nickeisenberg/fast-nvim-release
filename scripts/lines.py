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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple

REPOROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPOROOT / "src"))

from fast_nvim_release.platforms import (  # noqa: E402
    OLD_GLIBC_TAGS,
    PLATFORMS,
    Platform,
    PlatformKey,
    WheelTag,
)

LINES_FILE = REPOROOT / "lines.json"
#: where fetch_nvim.py unpacks a build, and where the tracked licenses live
NVIM_DIR = REPOROOT / "src" / "fast_nvim_release" / "_nvim"
BuildKind = Literal["official", "old_glibc"]
KINDS: Tuple[BuildKind, ...] = ("official", "old_glibc")
REPOS: Dict[BuildKind, str] = {
    "official": "neovim/neovim",
    "old_glibc": "neovim/neovim-releases",
}


@dataclass(frozen=True)
class BuildSpec:
    """One kind's pin inside a line: what ``lines.json`` records."""

    version: str  #: the Neovim release, e.g. ``v0.12.6``
    sha256: Dict[PlatformKey, str]  #: platform key -> archive checksum
    skip: Dict[PlatformKey, str] = field(
        default_factory=dict
    )  #: no wheel, and why
    note: Optional[str] = (
        None  #: free prose, e.g. how a checksum was established
    )

    def to_dict(self) -> dict:
        data: dict = {"version": self.version, "sha256": dict(self.sha256)}
        if self.skip:
            data["skip"] = dict(self.skip)
        if self.note:
            data["note"] = self.note
        return data

    @classmethod
    def from_dict(cls, data: dict) -> BuildSpec:
        return cls(
            version=data["version"],
            sha256=dict(data["sha256"]),
            skip=dict(data.get("skip", {})),
            note=data.get("note"),
        )


@dataclass(frozen=True)
class LineSpec:
    """One Neovim line, e.g. ``0.12``: its official build and, if any, the glibc 2.17 one."""

    official: BuildSpec
    old_glibc: Optional[BuildSpec] = None

    def pin(self, kind: BuildKind) -> Optional[BuildSpec]:
        """The pin for ``kind`` (``None`` if this line has no such build)."""
        return self.old_glibc if kind == "old_glibc" else self.official

    def to_dict(self) -> dict:
        data: dict = {"official": self.official.to_dict()}
        if self.old_glibc:
            data["old_glibc"] = self.old_glibc.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: dict) -> LineSpec:
        old_glibc = data.get("old_glibc")
        return cls(
            official=BuildSpec.from_dict(data["official"]),
            old_glibc=BuildSpec.from_dict(old_glibc) if old_glibc else None,
        )


@dataclass(frozen=True)
class LineJson:
    """``lines.json``: the revision, and every supported line's pinned builds."""

    revision: int
    lines: Dict[str, LineSpec]

    def to_dict(self) -> dict:
        return {
            "revision": self.revision,
            "lines": {
                line: self.lines[line].to_dict()
                for line in sorted(self.lines, key=line_key)
            },
        }

    @classmethod
    def from_dict(cls, data: dict) -> LineJson:
        return cls(
            revision=data["revision"],
            lines={
                line: LineSpec.from_dict(spec)
                for line, spec in data["lines"].items()
            },
        )

    def save(self, path: Path = LINES_FILE) -> None:
        path.write_text(
            json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8"
        )

    @classmethod
    def load(cls, path: Path = LINES_FILE) -> LineJson:
        with path.open(encoding="utf-8") as handle:
            return cls.from_dict(json.load(handle))


@dataclass(frozen=True)
class Build:
    kind: BuildKind
    version: str  #: the Neovim release, e.g. ``v0.12.6``
    sha256: Dict[PlatformKey, str]  #: platform key -> archive checksum

    @property
    def repo(self) -> str:
        return REPOS[self.kind]

    def url(self, platform_key: PlatformKey) -> str:
        asset = PLATFORMS[platform_key].asset
        return f"https://github.com/{self.repo}/releases/download/{self.version}/{asset}"


@dataclass(frozen=True)
class Wheel:
    line: str  #: e.g. ``0.12``
    version: str  #: the package version, e.g. ``0.12.6.1``
    tag: WheelTag  #: the wheel's platform tag
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


def load(path: Path = LINES_FILE) -> LineJson:
    """``lines.json`` as a :class:`LineJson`, lines oldest first."""
    return LineJson.load(path)


def save(data: LineJson, path: Path = LINES_FILE) -> None:
    data.save(path)


def license_name(line: str) -> str:
    """The license file each wheel of ``line`` carries, tracked in ``NVIM_DIR``."""
    return f"LICENSE.nvim-{line}.txt"


def license_url(line: str, data: LineJson) -> str:
    """Neovim's license at the tag of the line's official build."""
    tag = data.lines[line].official.version
    return f"https://raw.githubusercontent.com/neovim/neovim/{tag}/LICENSE.txt"


def newest_line(data: Optional[LineJson] = None) -> str:
    return list((data or load()).lines)[-1]


def builds(spec: LineSpec) -> List[Build]:
    return [
        Build(kind, pin.version, dict(pin.sha256))
        for kind in KINDS
        if (pin := spec.pin(kind)) is not None
    ]


def package_version(build: Build, revision: int) -> str:
    """``v0.12.6`` with revision 1 -> ``0.12.6.1``."""
    return f"{build.version.lstrip('v')}.{revision}"


def wheels(data: Optional[LineJson] = None) -> List[Wheel]:
    """Every wheel ``lines.json`` describes, newest line last."""
    data = data or load()
    found = []
    for line, spec in data.lines.items():
        for build in builds(spec):
            version = package_version(build, data.revision)
            for key, platform in PLATFORMS.items():
                if key not in build.sha256:
                    continue
                tag = (
                    platform.wheel_tag
                    if build.kind == "official"
                    else OLD_GLIBC_TAGS[key]
                )
                found.append(Wheel(line, version, tag, platform, build))
    return found


def find(line: str, tag: str, data: Optional[LineJson] = None) -> Wheel:
    """The wheel for ``line`` with platform ``tag``; ``KeyError`` saying what exists."""
    candidates = [wheel for wheel in wheels(data) if wheel.line == line]
    for wheel in candidates:
        if wheel.tag == tag:
            return wheel
    have = (
        ", ".join(wheel.tag for wheel in candidates) or "none (unknown line)"
    )
    raise KeyError(f"Neovim {line} has no build for {tag} (it has: {have})")
