"""Create or extend one GitHub release per package version among the given wheels.

    python -m release_tool release-notes [--dry-run] dist/*.whl

Each release is tagged ``v<version>`` (``v0.12.6.1``) and lists, per wheel, the
Neovim build inside.  When wheels are added to a version that already has a
release (the old-glibc builds catching up), they are appended to its notes.
Needs ``gh`` with ``GH_TOKEN`` and ``GITHUB_SHA`` set (the release workflow
does that).
"""

from __future__ import annotations

import argparse
import collections
import os
import subprocess
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

from nvim_bin.bundle import BuildJson


def entries(wheels: List[Path]) -> Dict[str, List[str]]:
    """version -> one markdown list item per wheel."""
    found = collections.defaultdict(list)
    for wheel in sorted(wheels):
        with zipfile.ZipFile(wheel) as zf:
            info = BuildJson.loads(zf.read("nvim_bin/_nvim/build.json"))
        found[info.version].append(f"- `{info.tag}`: Neovim {info.neovim}")
    return dict(found)


def body(version: str, items: List[str]) -> str:
    neovim = version.rsplit(".", 1)[0]
    line = neovim.rsplit(".", 1)[0]
    return (
        f'Install: `pip install "nvim-bin=={neovim}.*"` (this Neovim), '
        f'or `"nvim-bin=={line}.*"` to follow the {line} line.\n\n'
        "Wheels:\n" + "\n".join(items) + "\n"
    )


def notes(wheels: List[Path]) -> Dict[str, str]:
    """version -> release notes (markdown) for a new release."""
    return {
        version: body(version, items)
        for version, items in entries(wheels).items()
    }


def extended(old: str, items: List[str]) -> Optional[str]:
    """``old`` notes with the ``items`` they lack; ``None`` if they lack none.

    A re-run of a release job must not list a wheel twice.
    """
    present = set(old.splitlines())
    new = [item for item in items if item not in present]
    if not new:
        return None
    return old.rstrip("\n") + "\n" + "\n".join(new) + "\n"


def existing_notes(tag: str) -> Optional[str]:
    result = subprocess.run(
        ["gh", "release", "view", tag, "--json", "body", "--jq", ".body"],
        capture_output=True,
        text=True,
    )
    return result.stdout if result.returncode == 0 else None


# --------------------------------------------------------------------------
# the command
# --------------------------------------------------------------------------
def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "release-notes",
        help="Create or extend nvim-bin's GitHub releases for these wheels.",
    )
    parser.add_argument(
        "wheels",
        nargs="+",
        type=Path,
        metavar="WHEEL",
        help="The wheels just published.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the notes; do not call gh.",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    for version, items in entries(args.wheels).items():
        tag = f"v{version}"
        if args.dry_run:
            print(f"--- {tag}\n{body(version, items)}")
            continue
        old = existing_notes(tag)
        if old is None:
            cmd = [
                "gh",
                "release",
                "create",
                tag,
                "--title",
                tag,
                "--notes",
                body(version, items),
            ]
            cmd += ["--target", os.environ.get("GITHUB_SHA", "HEAD")]
        else:
            notes_ = extended(old, items)
            if notes_ is None:
                print(f"{tag}: already lists every wheel")
                continue
            cmd = ["gh", "release", "edit", tag, "--notes", notes_]
        subprocess.run(cmd, check=True)
