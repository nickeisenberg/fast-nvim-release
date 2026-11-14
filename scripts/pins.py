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
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import lines
from lines import BuildKind

from fast_nvim_release.platforms import OLD_GLIBC_TAGS, PLATFORMS, PlatformKey

API = "https://api.github.com/repos/{}/releases?per_page=100&page={}"
TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
SHA256 = re.compile(r"\b([0-9a-f]{64})\b")


# --------------------------------------------------------------------------
# asking GitHub
# --------------------------------------------------------------------------
def _get(url: str) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": "fast-nvim-release-pins"}
    )
    token = os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        # unredirected: release downloads redirect to another host
        request.add_unredirected_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def fetch_releases(repo: str) -> Dict[str, dict]:
    """tag -> release, for the stable releases of ``repo``."""
    found: Dict[str, dict] = {}
    for page in range(1, 6):
        batch = json.loads(_get(API.format(repo, page)))
        for release in batch:
            if not (release["draft"] or release["prerelease"]) and TAG.match(
                release["tag_name"]
            ):
                found[release["tag_name"]] = release
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


def checksums(release: dict, platform_keys) -> Dict[str, str]:
    """platform -> sha256 of its archive in ``release``, from GitHub's digests."""
    assets = {asset["name"]: asset for asset in release.get("assets", [])}
    found = {}
    for key in platform_keys:
        asset = assets.get(PLATFORMS[key].asset)
        if asset is None:
            # the pin still moves; the diff in the pins PR shows the dropped platform
            print(
                f"warning: {release['tag_name']} has no {PLATFORMS[key].asset}; "
                f"{key} gets no wheel for it",
                file=sys.stderr,
            )
            continue
        digest = asset.get("digest") or ""
        if digest.startswith("sha256:"):
            found[key] = digest.split(":", 1)[1]
            continue
        sumfile = assets.get(PLATFORMS[key].asset + ".sha256sum")
        match = (
            SHA256.search(_get(sumfile["browser_download_url"]).decode())
            if sumfile
            else None
        )
        if match is None:
            raise SystemExit(
                f"{release['tag_name']}: no published checksum for {asset['name']}"
            )
        found[key] = match.group(1)
    return found


# --------------------------------------------------------------------------
# the commands
# --------------------------------------------------------------------------
KINDS: Dict[BuildKind, List[PlatformKey]] = {
    "official": list(PLATFORMS),
    "old_glibc": list(OLD_GLIBC_TAGS),
}


def plan(
    data: dict, upstream: Dict[str, Dict[str, dict]]
) -> List[Tuple[str, BuildKind, str]]:
    """``(line, kind, newest tag)`` for every pin that is behind, plus new lines."""
    changes = []
    specs = data["lines"]
    newest = {kind: newest_patches(upstream[kind]) for kind in KINDS}
    supported = max(lines.line_key(line) for line in specs)
    for line in sorted(
        set(specs) | set(newest["official"]), key=lines.line_key
    ):
        if line not in specs and lines.line_key(line) < supported:
            continue  # an old line we never supported
        for kind in KINDS:
            tag = newest[kind].get(line)
            pinned = (specs.get(line, {}).get(kind) or {}).get("version")
            if tag and tag != pinned:
                changes.append((line, kind, tag))
    return changes


def apply(
    data: dict,
    upstream: Dict[str, Dict[str, dict]],
    changes: List[Tuple[str, BuildKind, str]],
) -> List[str]:
    """Edit ``data`` in place; return the lines whose pins moved."""
    touched = []
    for line, kind, tag in changes:
        spec = data["lines"].setdefault(line, {})
        skip = (spec.get(kind) or {}).get("skip", {})
        wanted = [key for key in KINDS[kind] if key not in skip]
        sha = checksums(upstream[kind][tag], wanted)
        if not sha:
            continue
        spec[kind] = {
            "version": tag,
            "sha256": sha,
            **({"skip": skip} if skip else {}),
        }
        if line not in touched:
            touched.append(line)
    return touched


def ensure_license(line: str, data: dict) -> None:
    path = lines.NVIM_DIR / lines.license_name(line)
    if not path.is_file():
        path.write_bytes(_get(lines.license_url(line, data)))


def lagging(data: dict) -> List[str]:
    return [
        f"{line}: the old-glibc wheels carry {spec['old_glibc']['version']}, "
        f"the others {spec['official']['version']}"
        for line, spec in data["lines"].items()
        if spec.get("old_glibc")
        and spec["old_glibc"]["version"] != spec["official"]["version"]
    ]


def cmd_check(args, data, upstream) -> int:
    changes = plan(data, upstream)
    for line, kind, tag in changes:
        pinned = (data["lines"].get(line, {}).get(kind) or {}).get(
            "version", "nothing"
        )
        print(
            f"behind: {line} {kind} pinned {pinned}, {lines.REPOS[kind]} has {tag}"
        )
    for note in lagging(data):
        print(f"note: {note}")
    if not changes:
        print("every pin is the newest patch")
    return 1 if changes and args.strict else 0


def cmd_update(args, data, upstream) -> int:
    touched = apply(data, upstream, plan(data, upstream))
    printed: List[str] = []
    for line in touched:
        ensure_license(line, data)
    for wheel in lines.wheels(data):
        if wheel.line in touched and wheel.version not in printed:
            printed.append(wheel.version)
            print(
                f"{wheel.line} -> {wheel.version} ({wheel.build.kind} {wheel.build.version})"
            )
    lines.save(data, args.file)
    if not touched:
        print("every pin is the newest patch")
    return 0


def cmd_bump(args, data, _upstream) -> int:
    data["revision"] += 1
    print(f"revision {data['revision']}; the next release publishes:")
    for version in sorted(
        {w.version for w in lines.wheels(data)}, key=lines.line_key
    ):
        print(f"  {version}")
    lines.save(data, args.file)
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

    data = lines.load(args.file)
    upstream: Optional[Dict[str, Dict[str, dict]]] = None
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
