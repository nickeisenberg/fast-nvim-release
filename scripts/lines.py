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
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple

REPOROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPOROOT / "src"))

from nvim_bin.platforms import (  # noqa: E402
    OLD_GLIBC_TAGS,
    PLATFORMS,
    Platform,
    PlatformKey,
    WheelTag,
)

LINES_FILE = REPOROOT / "lines.json"
#: where fetch_nvim.py unpacks a build, and where the tracked licenses live
NVIM_DIR = REPOROOT / "src" / "nvim_bin" / "_nvim"
BuildKind = Literal["official", "old_glibc"]
#: kind -> the platforms that kind of build can exist for
KIND_PLATFORMS: Dict[BuildKind, List[PlatformKey]] = {
    "official": list(PLATFORMS),
    "old_glibc": list(OLD_GLIBC_TAGS),
}
KINDS: Tuple[BuildKind, ...] = tuple(KIND_PLATFORMS)
REPOS: Dict[BuildKind, str] = {
    "official": "neovim/neovim",
    "old_glibc": "neovim/neovim-releases",
}
#: a Neovim release tag, e.g. ``v0.12.6`` (not ``nightly`` or ``stable``)
TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


@dataclass(frozen=True)
class BuildPin:
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
    def from_dict(cls, data: dict) -> BuildPin:
        return cls(
            version=data["version"],
            sha256=dict(data["sha256"]),
            skip=dict(data.get("skip", {})),
            note=data.get("note"),
        )


@dataclass(frozen=True)
class LineSpec:
    """One Neovim line, e.g. ``0.12``: its official build and, if any, the glibc 2.17 one."""

    official: BuildPin
    old_glibc: Optional[BuildPin] = None

    def pin(self, kind: BuildKind) -> Optional[BuildPin]:
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
            official=BuildPin.from_dict(data["official"]),
            old_glibc=BuildPin.from_dict(old_glibc) if old_glibc else None,
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
                for line in sorted(self.lines, key=version_tuple)
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


@dataclass(frozen=True)
class Wheel:
    line: str  #: e.g. ``0.12``
    version: str  #: the package version, e.g. ``0.12.6.1``
    tag: WheelTag  #: the wheel's platform tag
    platform: Platform
    kind: BuildKind  #: which repository the build comes from
    pin: BuildPin  #: the build, as ``lines.json`` pins it

    @property
    def neovim(self) -> str:
        """The Neovim release this wheel carries, e.g. ``v0.12.6``."""
        return self.pin.version

    @property
    def repo(self) -> str:
        return REPOS[self.kind]

    @property
    def sha256(self) -> str:
        """The pinned checksum of this wheel's Neovim archive."""
        return self.pin.sha256[self.platform.key]

    @property
    def url(self) -> str:
        """Where this wheel's Neovim archive is downloaded from."""
        return (
            f"https://github.com/{self.repo}/releases/download/"
            f"{self.neovim}/{self.platform.asset}"
        )

    @property
    def label(self) -> str:
        return f"{self.version} {self.tag}"

    @property
    def filename(self) -> str:
        return f"nvim_bin-{self.version}-py3-none-{self.tag}.whl"


# --------------------------------------------------------------------------
# versions
# --------------------------------------------------------------------------
def version_tuple(version: str) -> Tuple[int, ...]:
    """``"0.12"`` -> ``(0, 12)``, ``"0.12.6.1"`` -> ``(0, 12, 6, 1)``.

    Versions compare correctly as numbers, not as text (``0.12`` > ``0.9``).
    """
    return tuple(int(part) for part in version.split("."))


def parse_tag(tag: str) -> Optional[Tuple[int, int, int]]:
    """``"v0.12.6"`` -> ``(0, 12, 6)``; ``None`` for ``nightly`` and the like."""
    match = TAG.match(tag)
    if match is None:
        return None
    major, minor, patch = (int(part) for part in match.groups())
    return (major, minor, patch)


def line_of(tag: str) -> str:
    """``"v0.12.6"`` -> ``"0.12"``, the line a release belongs to."""
    version = parse_tag(tag)
    if version is None:
        raise ValueError(f"not a Neovim release tag: {tag!r}")
    major, minor, _ = version
    return f"{major}.{minor}"


def package_version(pin: BuildPin, revision: int) -> str:
    """``v0.12.6`` with revision 1 -> ``0.12.6.1``."""
    return f"{pin.version.lstrip('v')}.{revision}"


# --------------------------------------------------------------------------
# lines.json and the wheels it describes
# --------------------------------------------------------------------------
def load_line_json(path: Path = LINES_FILE) -> LineJson:
    """``lines.json`` as a :class:`LineJson`, lines oldest first."""
    with path.open(encoding="utf-8") as handle:
        return LineJson.from_dict(json.load(handle))


def save_line_json(line_json: LineJson, path: Path = LINES_FILE) -> None:
    path.write_text(
        json.dumps(line_json.to_dict(), indent=2) + "\n", encoding="utf-8"
    )


def license_name(line: str) -> str:
    """The license file each wheel of ``line`` carries, tracked in ``NVIM_DIR``."""
    return f"LICENSE.nvim-{line}.txt"


def license_url(line: str, data: LineJson) -> str:
    """Neovim's license at the tag of the line's official build."""
    tag = data.lines[line].official.version
    return f"https://raw.githubusercontent.com/neovim/neovim/{tag}/LICENSE.txt"


def newest_line(line_json: Optional[LineJson] = None) -> str:
    return list((line_json or load_line_json()).lines)[-1]


def wheel_tag(kind: BuildKind, platform: Platform) -> WheelTag:
    """The platform tag of a ``kind`` build's wheel for ``platform``."""
    if kind == "official":
        return platform.wheel_tag
    return OLD_GLIBC_TAGS[platform.key]


def wheels(line_json: Optional[LineJson] = None) -> List[Wheel]:
    """Every wheel ``lines.json`` describes, newest line last."""
    line_json = line_json or load_line_json()
    found = []
    for line, spec in line_json.lines.items():
        for kind in KINDS:
            pin = spec.pin(kind)
            if pin is None:
                continue
            version = package_version(pin, line_json.revision)
            for key, platform in PLATFORMS.items():
                if key in pin.sha256:
                    tag = wheel_tag(kind, platform)
                    found.append(
                        Wheel(line, version, tag, platform, kind, pin)
                    )
    return found


def find(line: str, tag: WheelTag, data: Optional[LineJson] = None) -> Wheel:
    """The wheel for ``line`` with platform ``tag``; ``KeyError`` saying what exists."""
    candidates = [wheel for wheel in wheels(data) if wheel.line == line]
    for wheel in candidates:
        if wheel.tag == tag:
            return wheel
    have = (
        ", ".join(wheel.tag for wheel in candidates) or "none (unknown line)"
    )
    raise KeyError(f"Neovim {line} has no build for {tag} (it has: {have})")
