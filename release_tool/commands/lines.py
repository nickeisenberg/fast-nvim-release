"""``lines.json``: what it pins, the wheels it describes, and keeping it current.

    python -m release_tool lines update --dry-run   # what would move; writes nothing
    python -m release_tool lines update             # move pins to the newest patches
    python -m release_tool lines bump               # after changing our installed code

``lines.json`` holds:

* ``revision``: one counter for this project's own code.  A wheel's version is
  the Neovim version it carries plus the revision: ``0.12.6.1`` is Neovim
  v0.12.6 with revision 1.  It goes up (``bump``) whenever the
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

``update`` asks GitHub for both repositories (``neovim/neovim`` for the
official builds, ``neovim/neovim-releases`` for the glibc 2.17 ones), moves
every pin to the newest patch of its line with the checksums GitHub
publishes, and adds new release lines with their license file.  A new patch
is a new package version by itself (``0.12.7.<revision>``), so ``update``
leaves the revision alone.  ``--dry-run`` does all of that but writes
nothing.  ``bump`` raises the revision, which re-releases every line's
current patch.  Set ``GITHUB_TOKEN`` to lift the anonymous API rate limit.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple

from nvim_bin.platforms import (
    OLD_GLIBC_TAGS,
    PLATFORMS,
    Platform,
    PlatformKey,
    WheelTag,
)
from release_tool import REPOROOT, gh_client
from release_tool.exceptions import ReleaseToolError
from release_tool.gh_client import Release

LINES_FILE = REPOROOT / "lines.json"
#: where ``fetch`` unpacks a build, and where the tracked licenses live
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
class WheelSpec:
    """One wheel ``lines.json`` describes: what to build, not a built file."""

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


def wheels(line_json: Optional[LineJson] = None) -> List[WheelSpec]:
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
                        WheelSpec(line, version, tag, platform, kind, pin)
                    )
    return found


def find(
    line: str, tag: WheelTag, data: Optional[LineJson] = None
) -> WheelSpec:
    """The wheel for ``line`` with platform ``tag``; ``KeyError`` saying what exists."""
    candidates = [wheel for wheel in wheels(data) if wheel.line == line]
    for wheel in candidates:
        if wheel.tag == tag:
            return wheel
    have = (
        ", ".join(wheel.tag for wheel in candidates) or "none (unknown line)"
    )
    raise KeyError(f"Neovim {line} has no build for {tag} (it has: {have})")


# --------------------------------------------------------------------------
# keeping lines.json in step with Neovim's releases
# --------------------------------------------------------------------------
SHA256 = re.compile(r"\b([0-9a-f]{64})\b")


@dataclass(frozen=True)
class Change:
    """A pin that is behind (or a new line): ``kind`` of ``line`` moves to ``tag``."""

    line: str
    kind: BuildKind
    tag: str


#: kind -> tag -> release
Upstream = Dict[BuildKind, Dict[str, Release]]


def neovim_releases(repo: str) -> Dict[str, Release]:
    """tag -> release, for ``repo``'s Neovim releases (``vX.Y.Z``, not ``nightly``)."""
    return {
        tag: release
        for tag, release in gh_client.releases(repo).items()
        if parse_tag(tag)
    }


def newest_patches(tags: list[str]) -> Dict[str, str]:
    """line -> newest tag, e.g. ``{"0.12": "v0.12.7"}``."""
    newest: Dict[str, Tuple[int, int, int]] = {}  # line -> newest version
    for tag in tags:
        version = parse_tag(tag)
        if version is None:
            continue  # nightly, stable, ...
        line = line_of(tag)
        if line not in newest or version > newest[line]:
            newest[line] = version
    return {
        line: "v{}.{}.{}".format(*version) for line, version in newest.items()
    }


def checksums(
    release: Release, platform_keys: List[PlatformKey]
) -> Dict[PlatformKey, str]:
    """platform -> sha256 of its archive in ``release``, from GitHub's digests."""
    found: Dict[PlatformKey, str] = {}
    for key in platform_keys:
        asset = release.assets.get(PLATFORMS[key].asset)
        if asset is None:
            # the pin still moves; the diff in the pins PR shows the dropped platform
            print(
                f"warning: {release.tag} has no {PLATFORMS[key].asset}; "
                f"{key} gets no wheel for it",
                file=sys.stderr,
            )
            continue
        if asset.sha256:
            found[key] = asset.sha256
            continue
        sumfile = release.assets.get(asset.name + ".sha256sum")
        match = (
            SHA256.search(gh_client.download_bytes(sumfile.url).decode())
            if sumfile
            else None
        )
        if match is None:
            raise ReleaseToolError(
                f"{release.tag}: no published checksum for {asset.name}"
            )
        found[key] = match.group(1)
    return found


# --------------------------------------------------------------------------
# the commands
# --------------------------------------------------------------------------
def gains_platforms(pin: BuildPin, kind: BuildKind, release: Release) -> bool:
    """True if ``release`` now has an archive for a platform ``pin`` lacks.

    A release can be missing an archive when the pin is made (still
    uploading, or a failed build re-run later); its platform comes back once
    the archive is there.  Platforms in ``skip`` stay out.
    """
    return any(
        key not in pin.sha256
        and key not in pin.skip
        and PLATFORMS[key].asset in release.assets
        for key in KIND_PLATFORMS[kind]
    )


def plan(line_json: LineJson, upstream: Upstream) -> List[Change]:
    """A change for every pin that is behind or incomplete, plus new lines."""
    specs = line_json.lines
    supported = max(version_tuple(line) for line in specs)

    newest: Dict[BuildKind, Dict[str, str]] = {}
    for glibc_kind in KINDS:
        tags = [release.tag for release in upstream[glibc_kind].values()]
        newest[glibc_kind] = newest_patches(tags)

    changes: List[Change] = []
    for line in sorted(
        set(specs) | set(newest["official"]), key=version_tuple
    ):
        if line not in specs and version_tuple(line) < supported:
            continue  # an old line we never supported

        for glibc_kind in KINDS:
            tag = newest[glibc_kind].get(line)
            pin = specs[line].pin(glibc_kind) if line in specs else None
            if not tag:
                continue
            if (
                pin is None
                or tag != pin.version
                or gains_platforms(pin, glibc_kind, upstream[glibc_kind][tag])
            ):
                changes.append(Change(line=line, kind=glibc_kind, tag=tag))

    return changes


def apply(
    data: LineJson, upstream: Upstream, changes: List[Change]
) -> Tuple[LineJson, List[str]]:
    """The edited data, and the lines whose pins moved."""
    specs = dict(data.lines)
    touched: List[str] = []
    for change in changes:
        spec = specs.get(change.line)
        if spec is None and change.kind != "official":
            continue  # a line is defined by its official build
        pin = spec.pin(change.kind) if spec else None
        skip = pin.skip if pin else {}
        # the same version gaining platforms: its pinned checksums (and the
        # note on how they were established) stay exactly as they are
        kept = pin if pin and pin.version == change.tag else None
        wanted: List[PlatformKey] = [
            key
            for key in KIND_PLATFORMS[change.kind]
            if key not in skip and not (kept and key in kept.sha256)
        ]
        found = checksums(upstream[change.kind][change.tag], wanted)
        merged: Dict[PlatformKey, str] = {
            **(kept.sha256 if kept else {}),
            **found,
        }
        sha: Dict[PlatformKey, str] = {
            key: merged[key]
            for key in KIND_PLATFORMS[change.kind]
            if key in merged
        }
        if not sha:
            continue
        new_pin = BuildPin(
            version=change.tag,
            sha256=sha,
            skip=skip,
            note=kept.note if kept else None,
        )
        if spec is None:
            specs[change.line] = LineSpec(official=new_pin)
        else:
            specs[change.line] = replace(spec, **{change.kind: new_pin})
        if change.line not in touched:
            touched.append(change.line)
    return replace(data, lines=specs), touched


def ensure_license(line: str, data: LineJson) -> None:
    path = NVIM_DIR / license_name(line)
    if not path.is_file():
        path.write_bytes(gh_client.download_bytes(license_url(line, data)))


def lagging(data: LineJson) -> List[str]:
    return [
        f"{line}: the old-glibc wheels carry {spec.old_glibc.version}, "
        f"the others {spec.official.version}"
        for line, spec in data.lines.items()
        if spec.old_glibc and spec.old_glibc.version != spec.official.version
    ]


def update(path: Path, dry_run: bool) -> None:
    """Move every pin to its line's newest patch (and add new lines)."""
    data = load_line_json(path)
    try:
        upstream: Upstream = {
            kind: neovim_releases(repo) for kind, repo in REPOS.items()
        }
    except Exception as exc:
        # any failure here means the releases could not be had: network,
        # a cut-off response, bad JSON or a changed format
        raise ReleaseToolError(
            f"cannot ask GitHub: {type(exc).__name__}: {exc}"
        ) from exc
    data, touched = apply(data, upstream, plan(data, upstream))
    printed: List[str] = []
    for wheel in wheels(data):
        if wheel.line in touched and wheel.version not in printed:
            printed.append(wheel.version)
            print(
                f"{wheel.line} -> {wheel.version} ({wheel.kind} {wheel.neovim})"
            )
    for note in lagging(data):
        print(f"note: {note}")
    if not touched:
        print("every pin is the newest patch")
    if dry_run:
        if touched:
            print("dry run: lines.json not written")
        return
    for line in touched:
        ensure_license(line, data)
    save_line_json(data, path)


def bump(path: Path) -> None:
    """Raise the revision: the next release re-releases every line's patch."""
    data = load_line_json(path)
    data = replace(data, revision=data.revision + 1)
    print(f"revision {data.revision}; the next release publishes:")
    for version in sorted(
        {w.version for w in wheels(data)}, key=version_tuple
    ):
        print(f"  {version}")
    save_line_json(data, path)


# --------------------------------------------------------------------------
# the command
# --------------------------------------------------------------------------
def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "lines", help="Keep lines.json's pinned builds current."
    )
    # tests point it at a copy of lines.json
    parser.add_argument(
        "--file", type=Path, default=LINES_FILE, help=argparse.SUPPRESS
    )

    lines_subparsers = parser.add_subparsers(
        dest="lines_command", required=True
    )

    update_parser = lines_subparsers.add_parser(
        "update",
        help="Move each pin to its line's newest patch, and add new lines.",
    )
    update_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would move, without writing anything.",
    )
    update_parser.set_defaults(func=run)

    bump_parser = lines_subparsers.add_parser(
        "bump",
        help="Raise the revision: re-release every line's current patch.",
    )
    bump_parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    if args.lines_command == "update":
        update(args.file, args.dry_run)
    elif args.lines_command == "bump":
        bump(args.file)
