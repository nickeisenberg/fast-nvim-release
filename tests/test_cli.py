"""The command line, driven through ``main``.  This is the stable interface."""

from __future__ import annotations

import os

import pytest
from conftest import POSIX_ONLY

from nvim_bin import __version__, bundle, cli
from nvim_bin.cli import main


def test_no_arguments_prints_help(capsys):
    assert main([]) == 0
    out = capsys.readouterr().out
    for command in ("setup", "run"):
        assert command in out


def test_there_is_no_path_command(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["path"])
    assert excinfo.value.code == 2


def test_version_names_the_bundled_neovim(fake_bundle, host, capsys):
    with pytest.raises(SystemExit):
        main(["--version"])
    out = capsys.readouterr().out
    assert f"nvim-bin {__version__}" in out
    assert "Neovim v0.12.6" in out and host.key in out


def test_version_without_a_bundled_build(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(bundle, "BUNDLE_ROOT", tmp_path / "empty")
    with pytest.raises(SystemExit):
        main(["--version"])
    assert "no Neovim build bundled" in capsys.readouterr().out


@POSIX_ONLY
def test_setup_installs_and_verifies(fake_bundle, prefix, capsys):
    assert main(["setup", "--prefix", str(prefix)]) == 0
    assert f"installed Neovim v0.12.6 into {prefix}" in capsys.readouterr().out
    assert bundle.installed(prefix).line == "0.12"


def test_setup_needs_a_prefix(fake_bundle, capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["setup"])
    assert excinfo.value.code == 2
    assert "--prefix" in capsys.readouterr().err


def test_setup_takes_no_release_argument(fake_bundle, prefix, capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["setup", "0.11", "--prefix", str(prefix)])
    assert excinfo.value.code == 2


def test_setup_refuses_foreign_files_without_force(
    fake_bundle, host, prefix, capsys
):
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin" / host.exe).write_text("mine", encoding="utf-8")
    assert main(["setup", "--prefix", str(prefix)]) == 1
    err = capsys.readouterr().err
    assert err.startswith("error: ") and "--force" in err


@POSIX_ONLY
def test_setup_again_takes_force(fake_bundle, prefix, capsys):
    assert main(["setup", "--prefix", str(prefix)]) == 0
    assert main(["setup", "--prefix", str(prefix)]) == 1
    err = capsys.readouterr().err
    assert "already holds Neovim v0.12.6" in err and "--force" in err
    assert main(["setup", "--prefix", str(prefix), "--force"]) == 0


@POSIX_ONLY
def test_a_symlinked_path_entry_counts_as_on_path(
    fake_bundle, prefix, tmp_path, monkeypatch, capsys
):
    link = tmp_path / "link"
    link.symlink_to(prefix, target_is_directory=True)
    prefix.mkdir()
    # the fake nvim is a shell script: it still needs the rest of PATH
    monkeypatch.setenv(
        "PATH", f"{link / 'bin'}{os.pathsep}{os.environ['PATH']}"
    )
    assert main(["setup", "--prefix", str(prefix)]) == 0
    assert "not on your PATH" not in capsys.readouterr().err


@pytest.fixture()
def launched(monkeypatch):
    """Capture what `run` would start instead of starting it."""
    calls = []

    def fake_exec(path, argv):
        calls.append((path, list(argv[1:])))
        return 0

    monkeypatch.setattr(cli.os, "execv", fake_exec)
    monkeypatch.setattr(
        cli.subprocess, "call", lambda argv: fake_exec(argv[0], argv)
    )
    return calls


@pytest.mark.parametrize(
    "argv, passed",
    [
        ([], []),
        (["file.txt"], ["file.txt"]),
        (["--", "--version"], ["--version"]),
        (["--headless", "+q"], ["--headless", "+q"]),
        (["0.11"], ["0.11"]),  # no release argument: a file named 0.11
    ],
)
def test_run_hands_everything_to_neovim(fake_bundle, launched, argv, passed):
    main(["run", *argv])
    path, args = launched[0]
    assert path == str(bundle.bundled_exe())
    assert args == passed
