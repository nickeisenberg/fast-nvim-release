"""Build the wheels described by ``lines.json``.

    python -m release_tool build --all
    python -m release_tool build --line 0.12 --wheel host
    python -m release_tool build --all --skip-published https://test.pypi.org

Each wheel is built in a throwaway copy of the source tree, so builds never mix
and the repo's own ``_nvim`` directory is left alone.  ``--skip-published``
leaves out every wheel file already on that index, so a release publishes
exactly what is new: a new Neovim patch, every line after a revision bump, or
old-glibc wheels added to a version once ``neovim-releases`` catches up.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Set

from nvim_bin.platforms import host_wheel_tag
from release_tool import REPOROOT
from release_tool.commands import lines
from release_tool.commands.lines import WheelSpec
from release_tool.exceptions import ReleaseToolError

NVIM = lines.NVIM_DIR.relative_to(REPOROOT)
IGNORE = shutil.ignore_patterns(
    "_nvim",
    ".git",
    "build",
    "dist",
    "*.egg-info",
    "__pycache__",
    ".*cache",
    ".venv",
    "venv",
)


def published(index: str, version: str) -> Set[str]:
    """The wheel files of ``version`` already on ``index`` (a PyPI-like site)."""
    url = f"{index.rstrip('/')}/pypi/nvim-bin/{version}/json"
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            release = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return set()
        raise
    return {entry["filename"] for entry in release.get("urls", [])}


def select(
    line_names: List[str], tags: List[str], skip_published: str = ""
) -> List[WheelSpec]:
    host = host_wheel_tag()
    wanted_tags = {host if tag == "host" else tag for tag in tags}
    chosen = [
        wheel
        for wheel in lines.wheels()
        if (not line_names or wheel.line in line_names)
        and (not tags or wheel.tag in wanted_tags)
    ]
    if skip_published:
        on_index: Dict[str, Set[str]] = {
            version: published(skip_published, version)
            for version in {w.version for w in chosen}
        }
        for wheel in chosen:
            if wheel.filename in on_index[wheel.version]:
                print(f"skipping {wheel.label}: already on {skip_published}")
        chosen = [w for w in chosen if w.filename not in on_index[w.version]]
    return chosen


def build(wheel: WheelSpec, outdir: Path) -> Path:
    print(f"==> {wheel.label}", flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "src-tree"
        shutil.copytree(REPOROOT, work, ignore=IGNORE)
        (work / NVIM).mkdir()
        license_file = lines.NVIM_DIR / lines.license_name(wheel.line)
        if license_file.is_file():  # else fetch says it is missing
            shutil.copy2(license_file, work / NVIM / license_file.name)
        # the copy's own release_tool, so the build lands in the copy
        fetch_cmd = [
            sys.executable,
            "-m",
            "release_tool",
            "fetch",
            "--line",
            wheel.line,
            "--wheel",
            wheel.tag,
        ]
        subprocess.run(fetch_cmd, cwd=work, check=True)
        built = Path(tmp) / "dist"
        cmd = [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--outdir",
            str(built),
        ]
        subprocess.run(cmd, cwd=work, check=True, stdout=subprocess.DEVNULL)
        (result,) = built.glob("*.whl")
        outdir.mkdir(parents=True, exist_ok=True)
        return Path(shutil.move(str(result), str(outdir / result.name)))


# --------------------------------------------------------------------------
# the command
# --------------------------------------------------------------------------
def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "build", help="Build wheels from the pinned builds."
    )
    parser.add_argument(
        "--all", action="store_true", help="Every wheel of every line."
    )
    parser.add_argument(
        "--line",
        action="append",
        default=[],
        help="Only this line, e.g. 0.12 (repeatable).",
    )
    parser.add_argument(
        "--wheel",
        action="append",
        default=[],
        metavar="TAG",
        help='Only this platform tag, or "host" (repeatable).',
    )
    parser.add_argument(
        "--skip-published",
        default="",
        metavar="INDEX_URL",
        help="Skip wheels that index already has, e.g. https://pypi.org.",
    )
    parser.add_argument(
        "--outdir",
        type=Path,
        default=REPOROOT / "dist",
        metavar="DIR",
        help="Where to put the wheels (default: dist).",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    # building everything is never the default: it is 20 wheels
    if not (args.all or args.line or args.wheel):
        raise ReleaseToolError("pass --all, or --line/--wheel")
    chosen = select(args.line, args.wheel, args.skip_published)
    if not chosen:
        print("nothing to build")
        return
    for wheel in chosen:
        print(f"    {build(wheel, args.outdir).name}", flush=True)
