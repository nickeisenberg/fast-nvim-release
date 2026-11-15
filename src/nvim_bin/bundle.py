"""Find the Neovim build inside the wheel and install it into a prefix.

Each wheel carries one complete Neovim tree, for one line and one platform::

    nvim_bin/_nvim/bin/nvim
    nvim_bin/_nvim/lib/nvim/...
    nvim_bin/_nvim/share/nvim/runtime/...
    nvim_bin/_nvim/build.json        which build this is

Neovim finds its runtime relative to its own executable
(``../share/nvim/runtime``), so ``setup`` copies the tree into the prefix with
that layout intact.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Iterable, Optional, Set, Tuple, Union

from nvim_bin.platforms import PLATFORMS, Platform, host_platform

BUNDLE_ROOT = Path(__file__).resolve().parent / "_nvim"

#: What ``setup`` puts into a prefix, besides the marker.
PARTS = ("bin", "lib/nvim", "share/nvim/runtime")

#: Records what this tool installed into a prefix.
MARKER = Path("share") / "nvim-bin" / "install.json"


@dataclass(frozen=True)
class BuildJson:
    version: str
    line: str
    neovim: str
    platform: str
    tag: str


class NvimBinError(RuntimeError):
    """Something the user can fix; reported without a traceback."""


# --------------------------------------------------------------------------
# the build inside this wheel
# --------------------------------------------------------------------------
def build_info() -> Optional[BuildJson]:
    """``build.json``, or ``None`` when this installation carries no build."""
    try:
        data = json.loads(
            (BUNDLE_ROOT / "build.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    names = [field.name for field in fields(BuildJson)]
    if not all(isinstance(data.get(name), str) for name in names):
        return None
    return BuildJson(*(data[name] for name in names))


def bundled_platform() -> Platform:
    """The platform of the bundled build (this machine's, if unrecorded)."""
    info = build_info()
    key = info.platform if info else ""

    if key in PLATFORMS:
        return PLATFORMS[key]
    here = host_platform()
    if here is None:
        raise NvimBinError("there is no Neovim build for this platform")
    return here


def bundled_exe() -> Path:
    """The bundled executable, or a :class:`NvimBinError` saying why there is none."""
    exe = BUNDLE_ROOT / "bin" / bundled_platform().exe
    if not exe.is_file():
        raise NvimBinError(
            "this installation carries no Neovim build "
            "(built from source without running scripts/fetch_nvim.py first?)"
        )
    return exe


# --------------------------------------------------------------------------
# prefixes
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class InstallJson:
    """``install.json``: what this tool put into a prefix, and what it owns."""

    line: Optional[str]
    nvim_version: Optional[str]
    platform: str
    bin: Tuple[str, ...]


def normalize(path: Union[str, os.PathLike]) -> Path:
    """Absolute, ``~``-expanded form of a user supplied path."""
    return Path(os.path.abspath(os.path.expanduser(os.fspath(path))))


def default_prefix() -> Path:
    """Where ``path`` looks when no ``--prefix`` is given (``setup`` has no default)."""
    if os.name == "nt":
        base = (
            os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
        )
        return normalize(Path(base) / "nvim-bin")
    return normalize(Path.home() / ".local")


def installed(prefix: Path) -> Optional[InstallJson]:
    """What this tool installed into ``prefix``, or ``None`` if it manages nothing.

    Only a marker this tool can read in full counts as an install: anything else
    must not pass for one, or ``setup`` would treat a prefix it never managed as
    its own and overwrite files there without ``--force``.
    """
    try:
        data = json.loads((prefix / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    if not isinstance(data, dict):
        return None

    line, version = data.get("line"), data.get("nvim_version")
    if not all(
        value is None or isinstance(value, str) for value in (line, version)
    ):
        return None

    platform, names = data.get("platform"), data.get("bin")
    if not isinstance(platform, str):
        return None

    if not isinstance(names, list) or not all(
        isinstance(name, str) for name in names
    ):
        return None

    return InstallJson(line, version, platform, tuple(names))


def _owned_bin_names(prefix: Path) -> Set[str]:
    """Entries of ``<prefix>/bin`` that the marker says this tool put there.

    The marker is a file users can edit, so only plain file names count.
    """
    info = installed(prefix)
    return {
        name
        for name in (info.bin if info else ())
        if name not in ("", ".", "..") and "/" not in name and "\\" not in name
    }


def _remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _copy(source: Path, dest: Path) -> None:
    if source.is_dir() and not source.is_symlink():
        shutil.copytree(source, dest, symlinks=True)
    else:
        shutil.copy2(source, dest, follow_symlinks=False)


def _write_marker(prefix: Path, bin_names: Iterable[str]) -> None:
    marker = prefix / MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)
    info = build_info()
    marker_info = InstallJson(
        line=info.line if info else None,
        nvim_version=info.neovim if info else None,
        platform=bundled_platform().key,
        bin=tuple(sorted(bin_names)),
    )
    marker.write_text(
        json.dumps(asdict(marker_info), indent=2) + "\n", encoding="utf-8"
    )


def install(prefix: Path, *, force: bool = False) -> Path:
    """Copy the bundled build into ``prefix`` and return the installed executable.

    A prefix holds one Neovim: installing into a prefix this tool already
    manages replaces the previous build, including the ``bin`` entries it
    owned.  Anything else is never overwritten without ``force``.
    """
    exe = bundled_exe()
    source = exe.parent.parent
    prefix = normalize(prefix)
    names = sorted(entry.name for entry in (source / "bin").iterdir())
    owned = _owned_bin_names(prefix)
    managed = installed(prefix) is not None

    if not force:
        # Windows file names ignore case, and releases differ in how they spell
        # the same DLL (dbghelp.dll vs DbgHelp.dll)
        owned_keys = {os.path.normcase(name) for name in owned}
        foreign = [
            prefix / "bin" / name
            for name in names
            if os.path.normcase(name) not in owned_keys
            and (
                (prefix / "bin" / name).exists()
                or (prefix / "bin" / name).is_symlink()
            )
        ]
        if not managed:
            foreign += [
                prefix / part for part in PARTS[1:] if (prefix / part).exists()
            ]
        if foreign:
            raise NvimBinError(
                "not installed by nvim-bin, so not overwritten:\n  "
                + "\n  ".join(str(path) for path in foreign)
                + "\n  re-run with --force to replace them, or pick another --prefix"
            )

    # claim everything first, so an interrupted install can be re-run without --force
    _write_marker(prefix, owned | set(names))
    for part in PARTS[1:]:
        _remove(prefix / part)
        if (source / part).is_dir():
            (prefix / part).parent.mkdir(parents=True, exist_ok=True)
            _copy(source / part, prefix / part)
    (prefix / "bin").mkdir(parents=True, exist_ok=True)
    for name in sorted(owned | set(names)):
        _remove(prefix / "bin" / name)
    for name in names:
        _copy(source / "bin" / name, prefix / "bin" / name)

    _write_marker(prefix, names)
    return prefix / "bin" / exe.name


#: ``-i NONE`` keeps Neovim from reading and rewriting the user's shada file.
PROBE_ARGS = (
    "-u",
    "NONE",
    "-i",
    "NONE",
    "--headless",
    "+lua io.write(vim.env.VIMRUNTIME)",
    "+q",
)


def verify(exe: Path, prefix: Path) -> None:
    """Start the installed Neovim and check that it found the prefix's runtime."""
    # $VIMRUNTIME / $VIM would override where Neovim looks for its runtime
    env = {
        k: v for k, v in os.environ.items() if k not in ("VIMRUNTIME", "VIM")
    }
    try:
        result = subprocess.run(
            [str(exe), *PROBE_ARGS], capture_output=True, timeout=60, env=env
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise NvimBinError(
            f"installed, but {exe} does not run: {exc}"
        ) from exc
    reported = result.stdout.decode("utf-8", "replace").strip()
    expected = (prefix / "share" / "nvim" / "runtime").resolve()
    if (
        result.returncode != 0
        or not reported
        or Path(reported).resolve() != expected
    ):
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise NvimBinError(
            f"installed, but {exe} did not start with the runtime in {expected}"
            f" (it reported {reported or 'nothing'!r}, exit {result.returncode})"
            + (f"\n{detail}" if detail else "")
        )
