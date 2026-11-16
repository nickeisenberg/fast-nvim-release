"""Installing the bundled build into a prefix."""

from __future__ import annotations

import json
import os

import pytest
from conftest import POSIX_ONLY, make_bundle

from nvim_bin import bundle


def runtime_text(prefix):
    return (prefix / "share" / "nvim" / "runtime" / "init.lua").read_text(
        encoding="utf-8"
    )


def test_build_info_and_exe(fake_bundle, host):
    info = bundle.build_info()
    assert info == bundle.BuildJson(
        version="0.12.6.1",
        line="0.12",
        neovim="v0.12.6",
        platform=host.key,
        tag=host.wheel_tag,
    )
    assert bundle.bundled_exe() == fake_bundle / "bin" / host.exe


def test_a_package_built_without_neovim_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(bundle, "BUNDLE_ROOT", tmp_path / "empty")
    assert bundle.build_info() is None
    with pytest.raises(bundle.NvimBinError, match=r"release_tool fetch"):
        bundle.bundled_exe()


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[]",
        '{"version": "0.12.6.1", "line": "0.12"}',
        '{"version": "0.12.6.1", "line": "0.12", "neovim": "v0.12.6",'
        ' "platform": "linux-x86_64", "tag": 1}',
    ],
)
def test_a_damaged_build_json_counts_as_no_build(
    tmp_path, monkeypatch, content
):
    monkeypatch.setattr(bundle, "BUNDLE_ROOT", tmp_path)
    (tmp_path / "build.json").write_text(content, encoding="utf-8")
    assert bundle.build_info() is None


def test_install_copies_the_tree_and_records_it(fake_bundle, host, prefix):
    exe = bundle.install(prefix)
    assert exe == prefix / "bin" / host.exe and exe.is_file()
    if os.name != "nt":
        assert os.access(exe, os.X_OK)
    assert (prefix / "lib" / "nvim" / "parser" / "c.so").is_file()
    assert runtime_text(prefix) == "-- v0.12.6\n"
    info = bundle.installed(prefix)
    assert info == bundle.InstallJson(
        package_version="0.12.6.1",
        line="0.12",
        nvim_version="v0.12.6",
        platform=host.key,
        bin=(host.exe,),
    )


def test_another_package_version_replaces_the_build_and_keeps_unrelated_files(
    fake_bundle, host, prefix
):
    (fake_bundle / "bin" / "helper").write_text("x", encoding="utf-8")
    bundle.install(prefix)  # owns bin/helper too
    (prefix / "bin" / "other-tool").write_text("mine", encoding="utf-8")

    # what `pip install "nvim-bin==0.11.*"` then `setup --force` looks like
    (fake_bundle / "bin" / "helper").unlink()
    make_bundle(fake_bundle, host, neovim="v0.11.7", version="0.11.7.1")
    bundle.install(prefix, force=True)
    assert runtime_text(prefix) == "-- v0.11.7\n"
    assert not (prefix / "bin" / "helper").exists()
    assert (prefix / "bin" / "other-tool").read_text(
        encoding="utf-8"
    ) == "mine"
    assert bundle.installed(prefix).line == "0.11"


def test_a_stale_runtime_file_does_not_survive_a_reinstall(
    fake_bundle, prefix
):
    bundle.install(prefix)
    stale = prefix / "share" / "nvim" / "runtime" / "stale.vim"
    stale.write_text("old", encoding="utf-8")
    bundle.install(prefix, force=True)
    assert not stale.exists()


@pytest.mark.parametrize(
    "existing", ["bin/{exe}", "bin/helper", "lib/nvim", "share/nvim/runtime"]
)
def test_files_this_tool_did_not_install_are_never_overwritten(
    fake_bundle, host, prefix, existing
):
    (fake_bundle / "bin" / "helper").write_text("ours", encoding="utf-8")
    target = prefix / existing.format(exe=host.exe)
    target.parent.mkdir(parents=True)
    if existing.startswith("bin/"):
        target.write_text("mine", encoding="utf-8")
    else:
        target.mkdir()
    with pytest.raises(bundle.NvimBinError, match="--force") as excinfo:
        bundle.install(prefix)
    assert str(target) in str(excinfo.value)
    assert bundle.installed(prefix) is None
    bundle.install(prefix, force=True)
    assert bundle.installed(prefix).line == "0.12"


@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("name", ["", "bin", "lib", "share/nvim"])
def test_a_file_where_setup_needs_a_directory_stops_it_before_it_writes(
    fake_bundle, prefix, name, force
):
    blocker = prefix / name
    blocker.parent.mkdir(parents=True, exist_ok=True)
    blocker.write_text("in the way", encoding="utf-8")
    with pytest.raises(bundle.NvimBinError, match="needs a directory") as exc:
        bundle.install(prefix, force=force)
    assert str(blocker) in str(exc.value)
    assert blocker.read_text(encoding="utf-8") == "in the way"
    if name:
        assert sorted(p.name for p in prefix.iterdir()) == [
            blocker.relative_to(prefix).parts[0]
        ]


def test_a_managed_prefix_still_protects_bin_entries_it_never_owned(
    fake_bundle, prefix
):
    bundle.install(prefix)
    mine = prefix / "bin" / "helper"
    mine.write_text("mine", encoding="utf-8")
    (fake_bundle / "bin" / "helper").write_text("new", encoding="utf-8")
    with pytest.raises(bundle.NvimBinError):
        bundle.install(prefix)
    assert mine.read_text(encoding="utf-8") == "mine"


def test_replacing_an_earlier_install_takes_force(fake_bundle, host, prefix):
    """Any replacement deletes something for good: its own installs too."""
    bundle.install(prefix)
    with pytest.raises(bundle.NvimBinError) as excinfo:
        bundle.install(prefix)
    message = str(excinfo.value)
    assert "already holds Neovim v0.12.6" in message
    assert host.key in message and "--force" in message
    assert str(prefix / "bin" / host.exe) in message
    bundle.install(prefix, force=True)


def test_a_marker_alone_takes_force(fake_bundle, prefix):
    marker = prefix / bundle.MARKER
    marker.parent.mkdir(parents=True)
    marker.write_text("damaged", encoding="utf-8")
    with pytest.raises(bundle.NvimBinError, match="--force"):
        bundle.install(prefix)


def test_bin_names_are_listed_once_the_way_the_file_system_compares_them(
    fake_bundle, prefix, monkeypatch
):
    """Windows: 0.10 ships dbghelp.dll, 0.11 DbgHelp.dll -- the same file there."""
    bundle.install(prefix)
    marker = prefix / bundle.MARKER
    data = json.loads(marker.read_text(encoding="utf-8"))
    data["bin"].append("dbghelp.dll")
    marker.write_text(json.dumps(data), encoding="utf-8")
    (prefix / "bin" / "DbgHelp.dll").write_text("ours", encoding="utf-8")
    (fake_bundle / "bin" / "DbgHelp.dll").write_text("new", encoding="utf-8")
    monkeypatch.setattr(bundle.os.path, "normcase", str.lower)  # as on Windows
    found = bundle._would_replace(
        prefix, ["DbgHelp.dll"], bundle._owned_bin_names(prefix)
    )
    assert [p.name.lower() for p in found].count("dbghelp.dll") == 1


def test_a_marker_it_cannot_read_in_full_counts_as_not_installed(
    fake_bundle, host, tmp_path
):
    """A half-written marker must not pass for an install this tool may overwrite."""
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / host.exe).write_text("mine", encoding="utf-8")
    marker = tmp_path / bundle.MARKER
    marker.parent.mkdir(parents=True)
    marker.write_text(
        json.dumps({"line": "0.12", "bin": [host.exe]}), encoding="utf-8"
    )
    assert bundle.installed(tmp_path) is None
    with pytest.raises(bundle.NvimBinError, match="--force"):
        bundle.install(tmp_path)
    assert (tmp_path / "bin" / host.exe).read_text(encoding="utf-8") == "mine"


def test_an_interrupted_install_is_cleaned_up_by_a_forced_rerun(
    fake_bundle, host, prefix, monkeypatch
):
    real_copy = bundle._copy

    def copy_then_fail(source, dest):
        real_copy(source, dest)
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(bundle, "_copy", copy_then_fail)
    with pytest.raises(OSError):
        bundle.install(prefix)
    monkeypatch.setattr(
        bundle, "_copy", real_copy
    )  # not undo(): keep fake_bundle
    with pytest.raises(bundle.NvimBinError, match="--force"):
        bundle.install(prefix)
    bundle.install(prefix, force=True)
    assert (prefix / "bin" / host.exe).is_file()
    assert bundle.installed(prefix).bin == (host.exe,)


@pytest.mark.parametrize(
    "names", ["nvim", ["../../escape", "/abs", "a\\b", ".."]]
)
def test_marker_bin_names_cannot_reach_outside_bin(fake_bundle, prefix, names):
    victim = prefix / "escape"
    victim.parent.mkdir(parents=True)
    victim.write_text("keep", encoding="utf-8")
    marker = prefix / bundle.MARKER
    marker.parent.mkdir(parents=True)
    marker.write_text(
        json.dumps(
            {
                "line": "0.12",
                "nvim_version": "v0.12.6",
                "platform": "linux-x86_64",
                "bin": names,
            }
        ),
        encoding="utf-8",
    )
    bundle.install(prefix, force=True)
    assert victim.read_text(encoding="utf-8") == "keep"


@POSIX_ONLY
def test_verify_accepts_a_healthy_install(fake_bundle, prefix):
    bundle.verify(bundle.install(prefix), prefix)


@POSIX_ONLY
def test_verify_notices_a_missing_runtime(fake_bundle, prefix):
    exe = bundle.install(prefix)
    bundle._remove(prefix / "share" / "nvim" / "runtime")
    with pytest.raises(
        bundle.NvimBinError, match="did not start with the runtime"
    ):
        bundle.verify(exe, prefix)


@POSIX_ONLY
def test_verify_ignores_the_users_vimruntime(host, tmp_path, monkeypatch):
    tree = make_bundle(tmp_path / "picky", host)
    exe = tree / "bin" / host.exe
    # a binary that, like real Neovim, prefers $VIMRUNTIME when it is set
    exe.write_text(
        '#!/bin/sh\nprintf "%s" "${VIMRUNTIME:-$(dirname "$0")/../share/nvim/runtime}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("VIMRUNTIME", "/somewhere/else")
    bundle.verify(exe, tree)


def test_an_older_marker_without_package_version_still_counts(
    fake_bundle, host, prefix
):
    bundle.install(prefix)
    marker = prefix / bundle.MARKER
    data = json.loads(marker.read_text(encoding="utf-8"))
    del data["package_version"]
    marker.write_text(json.dumps(data), encoding="utf-8")
    info = bundle.installed(prefix)
    assert info is not None and info.package_version is None


@POSIX_ONLY
def test_the_installed_executable_is_executable(fake_bundle, host, prefix):
    """Even when the installer dropped the executable bit of the wheel's file."""
    (fake_bundle / "bin" / host.exe).chmod(0o644)
    exe = bundle.install(prefix)
    assert os.access(exe, os.X_OK)
