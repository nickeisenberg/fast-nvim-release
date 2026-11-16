"""Install built wheels into a clean venv and use them like a user would.

    python -m release_tool smoke WHEEL [WHEEL...]

For each wheel (it must run on this machine): ``--version``, ``run``,
``setup`` and ``path``, and the installed nvim must report the Neovim version
the wheel's ``build.json`` names.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
import venv
import zipfile
from pathlib import Path

from nvim_bin.bundle import BuildJson
from release_tool.exceptions import ReleaseToolError

#: a user's $VIMRUNTIME must not decide whether the smoke test passes
ENV = {k: v for k, v in os.environ.items() if k not in ("VIMRUNTIME", "VIM")}


def execute(*cmd, ok: bool = True) -> str:
    print("$", *cmd, flush=True)
    result = subprocess.run(
        [str(part) for part in cmd],
        capture_output=True,
        text=True,
        timeout=300,
        env=ENV,
    )
    if (result.returncode == 0) != ok:
        raise ReleaseToolError(
            f"FAILED (exit {result.returncode}):\n{result.stdout}{result.stderr}"
        )
    return result.stdout + result.stderr


def smoke(wheel: Path) -> None:
    with zipfile.ZipFile(wheel) as zf:
        info = BuildJson.loads(zf.read("nvim_bin/_nvim/build.json"))
    neovim, version = info.neovim, info.version
    exe = "nvim.exe" if os.name == "nt" else "nvim"
    with tempfile.TemporaryDirectory(prefix="nvim-bin-smoke-") as tmp:
        work = Path(tmp)
        venv.create(work / "venv", with_pip=True)
        scripts = work / "venv" / ("Scripts" if os.name == "nt" else "bin")
        suffix = ".exe" if os.name == "nt" else ""
        python, cli = (
            scripts / f"python{suffix}",
            scripts / f"nvim-bin{suffix}",
        )
        execute(python, "-m", "pip", "install", "--no-deps", "-q", wheel)

        out = execute(cli, "--version")
        assert version in out and neovim in out, out
        assert neovim in execute(cli, "run", "--", "--version").splitlines()[0]
        execute(cli, "setup", ok=False)  # --prefix is required
        prefix = work / "prefix"
        execute(cli, "setup", "--prefix", prefix)
        assert (
            neovim
            in execute(prefix / "bin" / exe, "--version").splitlines()[0]
        )
        assert str(prefix) in execute(cli, "path", "--prefix", prefix)
    print(f"PASS {wheel.name} (Neovim {neovim})")


# --------------------------------------------------------------------------
# the command
# --------------------------------------------------------------------------
def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "smoke", help="Install built wheels in a clean venv and run them."
    )
    parser.add_argument(
        "wheels",
        nargs="+",
        type=Path,
        metavar="WHEEL",
        help="Wheels that run on this machine.",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    for wheel in args.wheels:
        smoke(wheel.resolve())
