#!/usr/bin/env python3
"""Create or extend one GitHub release per package version among the given wheels.

    python scripts/release_notes.py dist/*.whl [--dry-run]

Each release is tagged ``v<version>`` (``v0.12.6.1``) and lists, per wheel, the
Neovim build inside.  When wheels are added to a version that already has a
release (the old-glibc builds catching up), they are appended to its notes.
Needs ``gh`` with ``GH_TOKEN`` and ``GITHUB_SHA`` set (the release workflow
does that).
"""

from __future__ import annotations

import collections
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Dict, List, Optional


def entries(wheels: List[Path]) -> Dict[str, List[str]]:
    """version -> one markdown list item per wheel."""
    found = collections.defaultdict(list)
    for wheel in sorted(wheels):
        with zipfile.ZipFile(wheel) as zf:
            info = json.loads(zf.read("nvim_bin/_nvim/build.json"))
        found[info["version"]].append(
            f"- `{info['tag']}`: Neovim {info['neovim']}"
        )
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


def existing_notes(tag: str) -> Optional[str]:
    result = subprocess.run(
        ["gh", "release", "view", tag, "--json", "body", "--jq", ".body"],
        capture_output=True,
        text=True,
    )
    return result.stdout if result.returncode == 0 else None


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    dry_run = "--dry-run" in args
    wheels = [Path(arg) for arg in args if arg != "--dry-run"]
    for version, items in entries(wheels).items():
        tag = f"v{version}"
        if dry_run:
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
            cmd = [
                "gh",
                "release",
                "edit",
                tag,
                "--notes",
                old.rstrip("\n") + "\n" + "\n".join(items) + "\n",
            ]
        subprocess.run(cmd, check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
