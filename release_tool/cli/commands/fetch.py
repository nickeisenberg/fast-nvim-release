"""Download, verify and unpack the Neovim build for one wheel.

    python -m release_tool fetch                      # newest line, this machine
    python -m release_tool fetch --line 0.11          # another line
    python -m release_tool fetch --line 0.11 --wheel win_arm64   # cross-build

The build is unpacked into ``src/nvim_bin/_nvim/`` (replacing
whatever was there) together with ``build.json``, which ``setup.py`` reads for
the package version and wheel tag.  Archives are cached in
``~/.cache/nvim-bin`` (``NVIM_BIN_CACHE_DIR``) and checked
against the sha256 pinned in ``lines.json`` every time they are used.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path

from nvim_bin.bundle import PARTS, BuildJson
from nvim_bin.platforms import Platform, host_platform, host_wheel_tag
from release_tool import gh_client
from release_tool.cli.commands import lines
from release_tool.cli.commands.lines import WheelSpec
from release_tool.exceptions import ReleaseToolError

DEST = lines.NVIM_DIR
#: files in ``DEST`` that are tracked in git, not produced by this script
TRACKED = (".gitkeep",)


def cache_dir() -> Path:
    override = os.environ.get("NVIM_BIN_CACHE_DIR")
    return (
        Path(override).expanduser()
        if override
        else Path.home() / ".cache" / "nvim-bin"
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def archive(wheel: WheelSpec) -> Path:
    """The wheel's Neovim release archive, cached and checksum-verified."""
    expected = wheel.sha256
    # keyed by checksum: both Neovim repositories publish e.g. v0.11.7/nvim-linux-x86_64.tar.gz
    path = cache_dir() / expected[:16] / wheel.platform.asset
    if not path.is_file() or sha256(path) != expected:
        print(f"  downloading {wheel.url}", flush=True)
        gh_client.download_file(wheel.url, path)
        actual = sha256(path)
        if actual != expected:
            path.unlink()
            raise ReleaseToolError(
                f"checksum mismatch for {wheel.neovim} {wheel.platform.asset}:\n"
                f"  pinned {expected}\n  got    {actual}"
            )
    return path


def clear(dest: Path) -> None:
    """Remove a previous build from ``dest``, keeping the tracked files."""
    if not dest.is_dir():
        return
    for entry in dest.iterdir():
        if entry.name in TRACKED or entry.name.startswith("LICENSE.nvim-"):
            continue
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()


def unpack(source: Path, platform: Platform, dest: Path) -> None:
    """Unpack the parts Neovim needs at runtime into ``dest``."""
    with tempfile.TemporaryDirectory() as tmp:
        if platform.asset.endswith(".zip"):
            with zipfile.ZipFile(source) as zf:
                zf.extractall(
                    tmp
                )  # zipfile never writes outside tmp or creates links
        else:
            with tarfile.open(source) as tar:
                # "data" rejects absolute paths, links leaving tmp, devices, setuid bits
                tar.extractall(tmp, filter="data")
        root = Path(tmp) / platform.archive_dir
        if not (root / "bin" / platform.exe).is_file():
            raise ReleaseToolError(
                f"{source.name} has no {platform.archive_dir}/bin/{platform.exe}"
            )
        for part in PARTS:
            if (root / part).is_dir():
                (dest / part).parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(root / part), str(dest / part))


def check_runs(exe: Path, version: str) -> None:
    result = subprocess.run(
        [str(exe), "--version"], capture_output=True, text=True, timeout=60
    )
    first = (result.stdout.splitlines() or [""])[0]
    if result.returncode or version not in first:
        reason = result.stderr.strip() or f"it printed {first!r}"
        raise ReleaseToolError(
            f"{exe} does not run here (expected {version}):\n  {reason}\n"
            "  building for another kind of machine? pass --wheel (see --help)"
        )


def runs_here(wheel: WheelSpec) -> bool:
    """True if this machine can start the build (so it is worth a test run)."""
    here = host_platform()
    if here is None or here.key != wheel.platform.key:
        return False
    return wheel.kind == "old_glibc" or wheel.tag == host_wheel_tag()


def fetch(wheel: WheelSpec, dest: Path = DEST) -> None:
    print(
        f"{wheel.version}: Neovim {wheel.neovim} ({wheel.repo}) for {wheel.tag}"
    )
    source = archive(wheel)
    dest.mkdir(parents=True, exist_ok=True)
    clear(dest)
    unpack(source, wheel.platform, dest)

    info = BuildJson(
        version=wheel.version,
        line=wheel.line,
        neovim=wheel.neovim,
        platform=wheel.platform.key,
        tag=wheel.tag,
    )
    (dest / "build.json").write_text(info.dumps(), encoding="utf-8")

    if runs_here(wheel):
        check_runs(dest / "bin" / wheel.platform.exe, wheel.neovim)

    # a line's license comes with its pins (lines.py update), tracked in git
    license_file = dest / lines.license_name(wheel.line)
    tracked = lines.NVIM_DIR / license_file.name
    if not license_file.is_file():
        if not tracked.is_file():
            raise ReleaseToolError(
                f"{tracked} is missing; it comes with the line's pins "
                "(python -m release_tool lines update)"
            )
        shutil.copy2(tracked, license_file)


# --------------------------------------------------------------------------
# the command
# --------------------------------------------------------------------------
def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "fetch",
        help="Download, verify and unpack one pinned build into src/.",
    )
    parser.add_argument(
        "--line", help="A Neovim line, e.g. 0.12 (default: the newest)."
    )
    parser.add_argument(
        "--wheel",
        metavar="TAG",
        help="A wheel platform tag (default: the one pip picks here).",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=DEST,
        metavar="DIR",
        help=f"Where to unpack (default: {DEST}).",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    if not hasattr(tarfile, "data_filter"):
        raise ReleaseToolError(
            "needs tarfile extraction filters (Python 3.12+, or 3.9.17+/3.10.12+)"
        )
    data = lines.load_line_json()
    tag = args.wheel or host_wheel_tag()
    if tag is None:
        raise ReleaseToolError("cannot detect this platform; pass --wheel")
    try:
        wheel = lines.find(args.line or lines.newest_line(data), tag, data)
    except KeyError as exc:
        raise ReleaseToolError(exc.args[0]) from None
    fetch(wheel, args.dest)
