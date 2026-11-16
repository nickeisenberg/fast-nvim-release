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
import stat
import subprocess
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Iterable, List, Optional, Set, Tuple, Union

from nvim_bin.platforms import PLATFORMS, Platform, host_platform

BUNDLE_ROOT = Path(__file__).resolve().parent / "_nvim"

#: What ``setup`` puts into a prefix, besides the marker.
PARTS = ("bin", "lib/nvim", "share/nvim/runtime")

#: Records what this tool installed into a prefix.
MARKER = Path("share") / "nvim-bin" / "install.json"


@dataclass(frozen=True)
class BuildJson:
    """``_nvim/build.json``: which build a wheel carries."""

    version: str  #: the package version, e.g. ``0.12.6.1``
    line: str  #: e.g. ``0.12``
    neovim: str  #: the Neovim release, e.g. ``v0.12.6``
    platform: str  #: the platform key, e.g. ``linux-x86_64``
    tag: str  #: the wheel's platform tag

    @classmethod
    def from_dict(cls, data: object) -> BuildJson:
        """``ValueError`` unless ``data`` has every field, each a string."""
        if not isinstance(data, dict):
            raise ValueError("build.json is not a JSON object")
        names = [field.name for field in fields(cls)]
        missing = [
            name for name in names if not isinstance(data.get(name), str)
        ]
        if missing:
            raise ValueError(f"build.json lacks {', '.join(missing)}")
        return cls(*(data[name] for name in names))

    @classmethod
    def loads(cls, text: Union[str, bytes]) -> BuildJson:
        """Parse ``build.json``; ``ValueError`` if it is malformed."""
        return cls.from_dict(json.loads(text))

    def dumps(self) -> str:
        return json.dumps(asdict(self), indent=2) + "\n"


class NvimBinError(RuntimeError):
    """Something the user can fix; reported without a traceback."""


# --------------------------------------------------------------------------
# the build inside this wheel
# --------------------------------------------------------------------------
def build_info() -> Optional[BuildJson]:
    """``build.json``, or ``None`` when this installation carries no build."""
    try:
        return BuildJson.loads(
            (BUNDLE_ROOT / "build.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None


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
            "(built from source without running python -m release_tool fetch first?)"
        )
    return exe


# --------------------------------------------------------------------------
# prefixes
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class InstallJson:
    """``install.json``: what this tool put into a prefix, and what it owns.

    A future revision tells marker layouts apart by ``package_version``: its
    last number is the revision of this code that wrote the marker (markers
    without it come from before the field existed).
    """

    package_version: Optional[str]  #: e.g. ``0.12.6.1``
    line: Optional[str]
    nvim_version: Optional[str]
    platform: str
    bin: Tuple[str, ...]


def normalize(path: Union[str, os.PathLike]) -> Path:
    """Absolute, ``~``-expanded form of a user supplied path."""
    return Path(os.path.abspath(os.path.expanduser(os.fspath(path))))


def installed(prefix: Path) -> Optional[InstallJson]:
    """What this tool installed into ``prefix``, or ``None`` if it manages nothing.

    Only a marker this tool can read in full counts as an install: ``setup
    --force`` deletes the ``bin`` entries a marker lists, so a damaged or
    foreign file must not pass for one.
    """
    try:
        data = json.loads((prefix / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    if not isinstance(data, dict):
        return None

    package_version = data.get("package_version")
    line, version = data.get("line"), data.get("nvim_version")
    if not all(
        value is None or isinstance(value, str)
        for value in (package_version, line, version)
    ):
        return None

    platform, names = data.get("platform"), data.get("bin")
    if not isinstance(platform, str):
        return None

    if not isinstance(names, list) or not all(
        isinstance(name, str) for name in names
    ):
        return None

    return InstallJson(package_version, line, version, platform, tuple(names))


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
        package_version=info.version if info else None,
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

    A prefix holds one Neovim.  Replacing anything already there, an earlier
    install of this tool included, deletes it for good, so it takes ``force``.
    Other entries of ``<prefix>/bin`` are never touched.
    """
    exe = bundled_exe()
    source = exe.parent.parent
    prefix = normalize(prefix)
    names = sorted(entry.name for entry in (source / "bin").iterdir())
    owned = _owned_bin_names(prefix)

    if not force:
        existing = _would_replace(prefix, names, owned)
        if existing:
            info = installed(prefix)
            held = (
                f"{prefix} already holds Neovim {info.nvim_version or '(unknown)'}"
                f" ({info.platform}), installed by nvim-bin.\n"
                if info
                else ""
            )
            raise NvimBinError(
                f"{held}setup would replace:\n  "
                + "\n  ".join(str(path) for path in existing)
                + "\n  re-run with --force to replace them, or pick another --prefix"
            )

    # claim everything first, so an interrupted install leaves nothing
    # behind that a re-run (with --force) would not clean up
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

    installed_exe = prefix / "bin" / exe.name
    if os.name != "nt":
        # not every installer keeps the executable bit of a wheel's files
        mode = installed_exe.stat().st_mode
        installed_exe.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    _write_marker(prefix, names)
    return installed_exe


def _exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _would_replace(
    prefix: Path, names: Iterable[str], owned: Set[str]
) -> List[Path]:
    """Everything in ``prefix`` that an install would delete or overwrite."""
    found = [prefix / MARKER] if _exists(prefix / MARKER) else []
    found += [prefix / part for part in PARTS[1:] if _exists(prefix / part)]
    seen = set()  # Windows file names ignore case: dbghelp.dll is DbgHelp.dll
    for name in sorted(set(names) | owned):
        path = prefix / "bin" / name
        if os.path.normcase(name) not in seen and _exists(path):
            seen.add(os.path.normcase(name))
            found.append(path)
    return found


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
