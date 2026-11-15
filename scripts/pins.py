#!/usr/bin/env python3
"""Keep ``lines.json`` in step with Neovim's releases, and bump the revision.

    python scripts/pins.py check [--strict]   # is every pin the newest patch?
    python scripts/pins.py update             # move pins to the newest patches
    python scripts/pins.py bump               # after changing our installed code

``update`` asks GitHub for both repositories (``neovim/neovim`` for the
official builds, ``neovim/neovim-releases`` for the glibc 2.17 ones), moves
every pin to the newest patch of its line with the checksums GitHub
publishes, and adds new release lines.  A new patch is a new package version
by itself (``0.12.7.<revision>``), so ``update`` leaves the revision alone.
``bump`` raises the revision, which re-releases every line's current patch.
Set ``GITHUB_TOKEN`` to lift the anonymous API rate limit.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import lines
from lines import BuildKind, BuildPin, LineJson, LineSpec

from nvim_bin.platforms import OLD_GLIBC_TAGS, PLATFORMS, PlatformKey

API = "https://api.github.com/repos/{}/releases?per_page=100&page={}"
TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
SHA256 = re.compile(r"\b([0-9a-f]{64})\b")


# --------------------------------------------------------------------------
# GitHub's releases, as far as the pins need them
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Asset:
    name: str
    sha256: Optional[str]  #: from GitHub's ``digest``, when it published one
    url: str  #: ``browser_download_url``

    @classmethod
    def from_dict(cls, data: dict) -> Asset:
        algorithm, _, value = (data.get("digest") or "").partition(":")
        return cls(
            name=data["name"],
            sha256=value if algorithm == "sha256" else None,
            url=data["browser_download_url"],
        )


@dataclass(frozen=True)
class Release:
    tag: str
    assets: Dict[str, Asset]  #: asset name -> asset

    @classmethod
    def from_dict(cls, data: dict) -> Release:
        assets = (Asset.from_dict(asset) for asset in data.get("assets", []))
        return cls(
            tag=data["tag_name"],
            assets={asset.name: asset for asset in assets},
        )


@dataclass(frozen=True)
class Change:
    """A pin that is behind (or a new line): ``kind`` of ``line`` moves to ``tag``."""

    line: str
    kind: BuildKind
    tag: str


#: kind -> tag -> release
Upstream = Dict[BuildKind, Dict[str, Release]]


# --------------------------------------------------------------------------
# asking GitHub
# --------------------------------------------------------------------------
def _get(url: str) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": "nvim-bin-pins"}
    )
    token = os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        # unredirected: release downloads redirect to another host
        request.add_unredirected_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def fetch_releases(repo: str) -> Dict[str, Release]:
    """tag -> release, for the stable releases of ``repo``."""
    found: Dict[str, Release] = {}
    for page in range(1, 6):
        batch = json.loads(_get(API.format(repo, page)))
        for release in batch:
            if not (release["draft"] or release["prerelease"]) and TAG.match(
                release["tag_name"]
            ):
                found[release["tag_name"]] = Release.from_dict(release)
        if len(batch) < 100:
            break
    return found


def newest_patches(tags) -> Dict[str, str]:
    """line -> newest tag, e.g. ``{"0.12": "v0.12.7"}``."""
    newest: Dict[str, Tuple[int, int, int]] = {}
    for tag in tags:
        match = TAG.match(tag)
        if match:
            major, minor, patch = (int(part) for part in match.groups())
            version = (major, minor, patch)
            line = f"{version[0]}.{version[1]}"
            newest[line] = max(newest.get(line, version), version)
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
        match = SHA256.search(_get(sumfile.url).decode()) if sumfile else None
        if match is None:
            raise SystemExit(
                f"{release.tag}: no published checksum for {asset.name}"
            )
        found[key] = match.group(1)
    return found


# --------------------------------------------------------------------------
# the commands
# --------------------------------------------------------------------------
KIND_KEYS: Dict[BuildKind, List[PlatformKey]] = {
    "official": list(PLATFORMS),
    "old_glibc": list(OLD_GLIBC_TAGS),
}


def plan(data: LineJson, upstream: Upstream) -> List[Change]:
    """A change for every pin that is behind, plus one for each new line."""
    changes: List[Change] = []
    specs = data.lines
    newest = {kind: newest_patches(upstream[kind]) for kind in KIND_KEYS}
    supported = max(lines.line_key(line) for line in specs)
    for line in sorted(
        set(specs) | set(newest["official"]), key=lines.line_key
    ):
        if line not in specs and lines.line_key(line) < supported:
            continue  # an old line we never supported
        for kind in KIND_KEYS:
            tag = newest[kind].get(line)
            pin = specs[line].pin(kind) if line in specs else None
            if tag and (pin is None or tag != pin.version):
                changes.append(Change(line=line, kind=kind, tag=tag))
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
        wanted = [key for key in KIND_KEYS[change.kind] if key not in skip]
        sha = checksums(upstream[change.kind][change.tag], wanted)
        if not sha:
            continue
        new_pin = BuildPin(version=change.tag, sha256=sha, skip=skip)
        if spec is None:
            specs[change.line] = LineSpec(official=new_pin)
        else:
            specs[change.line] = replace(spec, **{change.kind: new_pin})
        if change.line not in touched:
            touched.append(change.line)
    return replace(data, lines=specs), touched


def ensure_license(line: str, data: LineJson) -> None:
    path = lines.NVIM_DIR / lines.license_name(line)
    if not path.is_file():
        path.write_bytes(_get(lines.license_url(line, data)))


def lagging(data: LineJson) -> List[str]:
    return [
        f"{line}: the old-glibc wheels carry {spec.old_glibc.version}, "
        f"the others {spec.official.version}"
        for line, spec in data.lines.items()
        if spec.old_glibc and spec.old_glibc.version != spec.official.version
    ]


def cmd_check(args, data, upstream) -> int:
    changes = plan(data, upstream)
    for change in changes:
        spec = data.lines.get(change.line)
        pin = spec.pin(change.kind) if spec else None
        pinned = pin.version if pin else "nothing"
        print(
            f"behind: {change.line} {change.kind} pinned {pinned}, "
            f"{lines.REPOS[change.kind]} has {change.tag}"
        )
    for note in lagging(data):
        print(f"note: {note}")
    if not changes:
        print("every pin is the newest patch")
    return 1 if changes and args.strict else 0


def cmd_update(args, data, upstream) -> int:
    data, touched = apply(data, upstream, plan(data, upstream))
    printed: List[str] = []
    for line in touched:
        ensure_license(line, data)
    for wheel in lines.wheels(data):
        if wheel.line in touched and wheel.version not in printed:
            printed.append(wheel.version)
            print(
                f"{wheel.line} -> {wheel.version} ({wheel.build.kind} {wheel.build.version})"
            )
    lines.save_line_json(data, args.file)
    if not touched:
        print("every pin is the newest patch")
    return 0


def cmd_bump(args, data, _upstream) -> int:
    data = replace(data, revision=data.revision + 1)
    print(f"revision {data.revision}; the next release publishes:")
    for version in sorted(
        {w.version for w in lines.wheels(data)}, key=lines.line_key
    ):
        print(f"  {version}")
    lines.save_line_json(data, args.file)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--file", type=Path, default=lines.LINES_FILE, help=argparse.SUPPRESS
    )
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="report pins that are behind")
    check.add_argument(
        "--strict", action="store_true", help="exit 1 if any pin is behind"
    )
    commands.add_parser("update", help="move pins to the newest patches")
    commands.add_parser(
        "bump", help="raise the revision (re-release every line)"
    )
    args = parser.parse_args(argv)

    data = lines.load_line_json(args.file)
    upstream: Optional[Upstream] = None
    if args.command != "bump":
        try:
            upstream = {
                kind: fetch_releases(repo)
                for kind, repo in lines.REPOS.items()
            }
        except (urllib.error.URLError, OSError, ValueError) as exc:
            print(f"error: cannot ask GitHub: {exc}", file=sys.stderr)
            return 2
    handler = {"check": cmd_check, "update": cmd_update, "bump": cmd_bump}[
        args.command
    ]
    return handler(args, data, upstream)


if __name__ == "__main__":
    sys.exit(main())
