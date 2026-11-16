"""release_tool: lines.json and its pins, fetching, wheel checks, releases."""

from __future__ import annotations

import hashlib
import http.client
import io
import json
import re
import struct
import tarfile
import zipfile
from dataclasses import replace

import pytest
from conftest import POSIX_ONLY

from nvim_bin.platforms import OLD_GLIBC_TAGS, PLATFORMS
from release_tool import gh_client
from release_tool.cli.commands import (
    build,
    check,
    fetch,
    lines,
    release_notes,
)
from release_tool.cli.main import main
from release_tool.exceptions import ReleaseToolError

LINUX = PLATFORMS["linux-x86_64"]
OLD_X86 = OLD_GLIBC_TAGS["linux-x86_64"]
NEEDS_TAR_FILTERS = pytest.mark.skipif(
    not hasattr(tarfile, "data_filter"),
    reason="this Python has no tarfile filters",
)


# --------------------------------------------------------------------------
# lines.json
# --------------------------------------------------------------------------
def test_lines_json_is_well_formed():
    data = lines.load_line_json()
    assert isinstance(data.revision, int) and data.revision >= 1
    specs = data.lines
    assert list(specs) == sorted(specs, key=lines.version_tuple)
    for line, spec in specs.items():
        assert re.fullmatch(r"\d+\.\d+", line)
        for kind in lines.KINDS:
            pin = spec.pin(kind)
            if pin is None:
                continue
            assert re.fullmatch(rf"v{re.escape(line)}\.\d+", pin.version)
            assert lines.line_of(pin.version) == line
            for key, digest in pin.sha256.items():
                assert key in lines.KIND_PLATFORMS[kind] and re.fullmatch(
                    r"[0-9a-f]{64}", digest
                )


def test_every_wheel_has_its_line_version_and_tag():
    data = lines.load_line_json()
    for wheel in lines.wheels(data):
        assert wheel.version == f"{wheel.neovim[1:]}.{data.revision}"
        assert wheel.version.startswith(wheel.line + ".")
        expected = (
            wheel.platform.wheel_tag
            if wheel.kind == "official"
            else OLD_GLIBC_TAGS[wheel.platform.key]
        )
        assert wheel.tag == expected
    labels = [(w.line, w.tag) for w in lines.wheels(data)]
    assert len(labels) == len(set(labels))


def test_known_gaps_have_no_wheel():
    tags = {(w.line, w.tag) for w in lines.wheels()}
    assert ("0.10", "manylinux_2_34_aarch64") not in tags  # needs glibc 2.38
    assert ("0.10", "win_arm64") not in tags  # never published
    assert (
        "0.11",
        OLD_GLIBC_TAGS["linux-arm64"],
    ) not in tags  # neovim-releases: 0.12.3+
    with pytest.raises(KeyError, match="has no build for win_arm64"):
        lines.find("0.10", "win_arm64")


def test_the_two_repositories_never_share_a_cache_entry():
    """Both publish v0.11.7/nvim-linux-x86_64.tar.gz; the files differ."""
    official = lines.find("0.11", LINUX.wheel_tag)
    old = lines.find("0.11", OLD_GLIBC_TAGS[LINUX.key])
    assert official.url != old.url
    assert official.sha256[:16] != old.sha256[:16]


def test_version_helpers():
    assert lines.version_tuple("0.12") > lines.version_tuple("0.9")
    assert lines.version_tuple("0.12.6.1") == (0, 12, 6, 1)
    assert lines.parse_tag("v0.12.10") == (0, 12, 10)
    assert lines.parse_tag("nightly") is None
    assert lines.line_of("v0.12.6") == "0.12"
    with pytest.raises(ValueError):
        lines.line_of("stable")


# --------------------------------------------------------------------------
# fetch
# --------------------------------------------------------------------------
def _tar(path, members, links=()):
    with tarfile.open(path, "w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755
            tar.addfile(info, io.BytesIO(data))
        for name, target in links:
            info = tarfile.TarInfo(name)
            info.type = tarfile.SYMTYPE
            info.linkname = target
            tar.addfile(info)
    return path


NVIM_TREE = {
    "nvim-linux-x86_64/bin/nvim": b"#!/bin/sh\necho 'NVIM v0.12.6'\n",
    "nvim-linux-x86_64/lib/nvim/parser/c.so": b"so",
    "nvim-linux-x86_64/share/nvim/runtime/init.lua": b"--",
    "nvim-linux-x86_64/share/man/man1/nvim.1": b"not needed at runtime",
}


@NEEDS_TAR_FILTERS
def test_unpack_keeps_only_what_neovim_needs(tmp_path):
    dest = tmp_path / "out"
    fetch.unpack(_tar(tmp_path / "a.tar.gz", NVIM_TREE), LINUX, dest)
    assert (dest / "bin" / "nvim").is_file()
    assert (dest / "share" / "nvim" / "runtime" / "init.lua").is_file()
    assert not (dest / "share" / "man").exists()


@NEEDS_TAR_FILTERS
def test_unpack_refuses_links_that_leave_the_archive(tmp_path):
    archive = _tar(
        tmp_path / "evil.tar.gz",
        NVIM_TREE,
        links=[
            ("nvim-linux-x86_64/lib/nvim/d", "../.."),
            ("nvim-linux-x86_64/lib/nvim/d/e", "../../../../.."),
        ],
    )
    with pytest.raises(tarfile.FilterError):
        fetch.unpack(archive, LINUX, tmp_path / "out")


def test_unpack_a_windows_zip(tmp_path):
    archive = tmp_path / "w.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("nvim-win64/bin/nvim.exe", b"MZ")
        zf.writestr("nvim-win64/share/nvim/runtime/init.lua", b"--")
    fetch.unpack(archive, PLATFORMS["windows-x86_64"], tmp_path / "out")
    assert (tmp_path / "out" / "bin" / "nvim.exe").read_bytes() == b"MZ"


def test_an_archive_that_does_not_match_its_pin_is_refused(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("NVIM_BIN_CACHE_DIR", str(tmp_path))

    def evil(url, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"evil")

    monkeypatch.setattr(gh_client, "download_file", evil)
    with pytest.raises(ReleaseToolError, match="checksum mismatch"):
        fetch.archive(lines.find("0.12", LINUX.wheel_tag))
    assert not list(tmp_path.rglob("*.tar.gz"))


def test_a_verified_cached_archive_is_reused(tmp_path, monkeypatch):
    monkeypatch.setenv("NVIM_BIN_CACHE_DIR", str(tmp_path))
    data = b"archive"
    digest = hashlib.sha256(data).hexdigest()
    wheel = replace(
        lines.find("0.12", LINUX.wheel_tag),
        pin=lines.BuildPin(version="v0.12.6", sha256={LINUX.key: digest}),
    )
    cached = tmp_path / digest[:16] / LINUX.asset
    cached.parent.mkdir()
    cached.write_bytes(data)
    monkeypatch.setattr(
        gh_client,
        "download_file",
        lambda url, dest: pytest.fail("downloaded"),
    )
    assert fetch.archive(wheel) == cached


@NEEDS_TAR_FILTERS
def test_fetch_writes_build_json_and_replaces_a_previous_build(
    tmp_path, monkeypatch
):
    dest = tmp_path / "_nvim"
    (dest / "0.12" / "linux-x86_64").mkdir(parents=True)  # leftovers
    (dest / "LICENSE.nvim-0.12.txt").write_text("Apache", encoding="utf-8")
    archive = _tar(tmp_path / "a.tar.gz", NVIM_TREE)
    monkeypatch.setattr(fetch, "archive", lambda wheel: archive)
    monkeypatch.setattr(fetch, "runs_here", lambda wheel: False)
    wheel = lines.find("0.12", OLD_X86)
    fetch.fetch(wheel, dest)
    info = json.loads((dest / "build.json").read_text(encoding="utf-8"))
    assert info == {
        "version": wheel.version,
        "line": "0.12",
        "neovim": wheel.neovim,
        "platform": "linux-x86_64",
        "tag": OLD_X86,
    }
    assert not (dest / "0.12").exists()
    assert (dest / "LICENSE.nvim-0.12.txt").is_file()


@NEEDS_TAR_FILTERS
def test_fetch_takes_the_tracked_license_and_never_downloads_one(
    tmp_path, monkeypatch
):
    tracked = tmp_path / "tracked"
    tracked.mkdir()
    monkeypatch.setattr(lines, "NVIM_DIR", tracked)
    archive = _tar(tmp_path / "a.tar.gz", NVIM_TREE)
    monkeypatch.setattr(fetch, "archive", lambda wheel: archive)
    monkeypatch.setattr(fetch, "runs_here", lambda wheel: False)
    monkeypatch.setattr(
        gh_client, "download_bytes", lambda url: pytest.fail("downloaded")
    )
    wheel = lines.find("0.12", OLD_X86)

    with pytest.raises(
        ReleaseToolError, match=r"LICENSE\.nvim-0\.12\.txt is missing"
    ):
        fetch.fetch(wheel, tmp_path / "a")

    (tracked / "LICENSE.nvim-0.12.txt").write_text("Apache", encoding="utf-8")
    fetch.fetch(wheel, tmp_path / "b")
    assert (tmp_path / "b" / "LICENSE.nvim-0.12.txt").read_text(
        encoding="utf-8"
    ) == "Apache"


@POSIX_ONLY
def test_a_build_that_cannot_run_here_says_why(tmp_path):
    exe = tmp_path / "nvim"
    exe.write_text(
        "#!/bin/sh\necho \"nvim: version 'GLIBC_2.34' not found\" >&2\nexit 1\n"
    )
    exe.chmod(0o755)
    with pytest.raises(ReleaseToolError) as excinfo:
        fetch.check_runs(exe, "v0.12.6")
    assert "GLIBC_2.34' not found" in str(excinfo.value)
    assert "--wheel" in str(excinfo.value)


# --------------------------------------------------------------------------
# check
# --------------------------------------------------------------------------
def fake_binary(key, glibc=b"GLIBC_2.34", macos=(11, 0)):
    """The start of an executable for platform ``key``, as far as check reads it."""
    system, arch = key.split("-")
    if system == "linux":
        return (
            b"\x7fELF"
            + bytes(14)
            + struct.pack("<H", {"x86_64": 0x3E, "arm64": 0xB7}[arch])
            + glibc
        )
    if system == "macos":
        cputype = {"x86_64": 0x01000007, "arm64": 0x0100000C}[arch]
        minos = macos[0] << 16 | macos[1] << 8
        header = struct.pack("<8I", 0xFEEDFACF, cputype, 0, 2, 1, 24, 0, 0)
        return header + struct.pack("<6I", 0x32, 24, 1, minos, 0, 0)
    machine = {"x86_64": 0x8664, "arm64": 0xAA64}[arch]
    return (
        b"MZ"
        + bytes(0x3A)
        + struct.pack("<I", 0x40)
        + b"PE\0\0"
        + struct.pack("<H", machine)
    )


def fake_wheel(
    tmp_path,
    wheel,
    glibc=None,
    runtime=1600,
    info_override=None,
    version=None,
    binary=None,
):
    version = version or wheel.version
    path = tmp_path / f"nvim_bin-{version}-py3-none-{wheel.tag}.whl"
    if glibc is None:
        glibc = b"GLIBC_2.17" if "2_17" in wheel.tag else b"GLIBC_2.34"
    if binary is None:
        binary = fake_binary(wheel.platform.key, glibc)
    info = {
        "version": wheel.version,
        "line": wheel.line,
        "neovim": wheel.neovim,
        "platform": wheel.platform.key,
        "tag": wheel.tag,
        **(info_override or {}),
    }
    with zipfile.ZipFile(path, "w") as zf:
        root = "nvim_bin/_nvim/"
        zf.writestr(root + "build.json", json.dumps(info))
        exe = zipfile.ZipInfo(f"{root}bin/{wheel.platform.exe}")
        exe.external_attr = 0o755 << 16
        zf.writestr(exe, binary)
        for n in range(runtime):
            zf.writestr(f"{root}share/nvim/runtime/f{n}.vim", b"")
        zf.writestr(f"{root}LICENSE.nvim-{wheel.line}.txt", b"Apache")
        meta = f"nvim_bin-{version}.dist-info/"
        tags = "".join(
            f"Tag: py3-none-{part}\n" for part in wheel.tag.split(".")
        )
        zf.writestr(meta + "WHEEL", f"Root-Is-Purelib: false\n{tags}")
        zf.writestr(meta + "entry_points.txt", "[console_scripts]\n")
    return path


@pytest.mark.parametrize("wheel", lines.wheels(), ids=lambda w: w.label)
def test_every_wheel_in_lines_json_passes_when_complete(tmp_path, wheel):
    assert check.check(fake_wheel(tmp_path, wheel)) == []


@pytest.mark.parametrize(
    "kwargs, problem",
    [
        ({"glibc": b"GLIBC_2.38"}, "needs glibc (2, 38)"),
        ({"runtime": 10}, "only 10 runtime files"),
        ({"info_override": {"neovim": "v0.12.1"}}, "build.json is"),
        ({"info_override": {"tag": None}}, "build.json lacks tag"),
        ({"version": "0.12.99.1"}, "lines.json says"),
        ({"version": "0.12.6.99"}, "lines.json says"),
    ],
)
def test_a_broken_wheel_fails(tmp_path, kwargs, problem):
    problems = check.check(
        fake_wheel(tmp_path, lines.find("0.12", LINUX.wheel_tag), **kwargs)
    )
    assert any(problem in item for item in problems), problems


@pytest.mark.parametrize(
    "line, tag, binary, problem",
    [
        (
            "0.12",
            LINUX.wheel_tag,
            fake_binary("linux-arm64"),
            "built for arm64, not x86_64",
        ),
        (
            "0.12",
            "win_arm64",
            fake_binary("windows-x86_64"),
            "built for x86_64, not arm64",
        ),
        ("0.12", "macosx_11_0_arm64", b"#!/bin/sh\n", "an unknown CPU"),
        (
            "0.12",
            "macosx_11_0_arm64",
            fake_binary("macos-arm64", macos=(13, 0)),
            "macOS (13, 0)",
        ),
    ],
)
def test_an_executable_that_cannot_run_under_its_tag_fails(
    tmp_path, line, tag, binary, problem
):
    problems = check.check(
        fake_wheel(tmp_path, lines.find(line, tag), binary=binary)
    )
    assert any(problem in item for item in problems), problems


def test_a_wheel_carries_only_its_own_lines_license(tmp_path):
    path = fake_wheel(tmp_path, lines.find("0.12", LINUX.wheel_tag))
    with zipfile.ZipFile(path, "a") as zf:
        zf.writestr("nvim_bin/_nvim/LICENSE.nvim-0.10.txt", b"Apache")
    problems = check.check(path)
    assert any("other lines' licenses" in item for item in problems), problems


def test_an_old_glibc_wheel_must_not_need_a_newer_glibc(tmp_path):
    wheel = lines.find("0.12", OLD_X86)
    problems = check.check(fake_wheel(tmp_path, wheel, glibc=b"GLIBC_2.34"))
    assert any("needs glibc (2, 34)" in item for item in problems), problems


# --------------------------------------------------------------------------
# build: which wheels a run builds
# --------------------------------------------------------------------------
def test_select_by_line_and_tag(monkeypatch):
    monkeypatch.setattr(build, "host_wheel_tag", lambda: LINUX.wheel_tag)
    chosen = build.select([], ["host"])
    assert {w.line for w in chosen} == set(lines.load_line_json().lines) and {
        w.tag for w in chosen
    } == {LINUX.wheel_tag}
    assert {w.tag for w in build.select(["0.12"], [])} == {
        w.tag for w in lines.wheels() if w.line == "0.12"
    }


def test_select_skips_wheels_already_published(monkeypatch):
    newest = lines.newest_line()
    on_index = {
        w.filename
        for w in lines.wheels()
        if not w.version.startswith(newest + ".")
    }
    monkeypatch.setattr(
        build,
        "published",
        lambda index, version: {
            name for name in on_index if f"-{version}-" in name
        },
    )
    chosen = build.select([], [], skip_published="https://test.pypi.org")
    assert chosen and {w.line for w in chosen} == {newest}


def test_select_adds_missing_files_to_an_existing_version(monkeypatch):
    """The old-glibc build catches up: only its wheels are new in that version."""
    official = {w.filename for w in lines.wheels() if "2_17" not in w.tag}
    monkeypatch.setattr(
        build,
        "published",
        lambda index, version: {
            name for name in official if f"-{version}-" in name
        },
    )
    chosen = build.select([], [], skip_published="https://pypi.org")
    assert chosen and all("2_17" in w.tag for w in chosen)


# --------------------------------------------------------------------------
# pins: following Neovim's releases
# --------------------------------------------------------------------------
def _release(tag, platform_keys, digests=True):
    assets = {}
    for key in platform_keys:
        name = PLATFORMS[key].asset
        digest = hashlib.sha256(f"{tag}{name}".encode()).hexdigest()
        assets[name] = gh_client.Asset(
            name=name,
            sha256=digest if digests else None,
            url=f"https://example.invalid/{tag}/{name}",
        )
    return gh_client.Release(tag=tag, stable=True, assets=assets)


def _upstream(official, old):
    return {
        "official": {t: _release(t, list(PLATFORMS)) for t in official},
        "old_glibc": {t: _release(t, ["linux-x86_64"]) for t in old},
    }


def test_a_release_is_read_from_githubs_json():
    release = gh_client.Release.from_dict(
        {
            "tag_name": "v0.12.6",
            "draft": False,
            "prerelease": False,
            "assets": [
                {
                    "name": "a.tar.gz",
                    "digest": "sha256:" + "ab" * 32,
                    "browser_download_url": "https://example.invalid/a",
                },
                {
                    "name": "b.zip",
                    "digest": None,
                    "browser_download_url": "https://example.invalid/b",
                },
            ],
        }
    )
    assert release.tag == "v0.12.6"
    assert release.stable
    assert release.assets["a.tar.gz"].sha256 == "ab" * 32
    assert release.assets["b.zip"].sha256 is None
    assert release.assets["b.zip"].url == "https://example.invalid/b"


@pytest.mark.parametrize(
    "draft, prerelease", [(True, False), (False, True), (True, True)]
)
def test_drafts_and_prereleases_are_not_stable(draft, prerelease):
    release = gh_client.Release.from_dict(
        {"tag_name": "v0.13.0", "draft": draft, "prerelease": prerelease}
    )
    assert not release.stable


def test_newest_patch_per_line():
    tags = ["v0.12.5", "v0.12.6", "v0.11.7", "nightly", "stable", "v0.12.10"]
    assert lines.newest_patches(tags) == {
        "0.12": "v0.12.10",
        "0.11": "v0.11.7",
    }


def with_pin(data, line, kind, **changes):
    """A copy of ``data`` with one line's pin for ``kind`` replaced."""
    spec = data.lines[line]
    pin = replace(spec.pin(kind), **changes)
    return replace(
        data, lines={**data.lines, line: replace(spec, **{kind: pin})}
    )


def test_update_moves_only_the_lines_that_changed():
    data = lines.load_line_json()
    specs = data.lines
    current = {line: spec.official.version for line, spec in specs.items()}
    old = {
        line: spec.old_glibc.version
        for line, spec in specs.items()
        if spec.old_glibc
    }
    upstream = _upstream(
        [*current.values(), "v0.12.99"],  # Neovim ships a 0.12 patch
        [*old.values()],
    )
    changes = lines.plan(data, upstream)
    assert changes == [lines.Change("0.12", "official", "v0.12.99")]
    revision = data.revision
    others = {line: spec for line, spec in specs.items() if line != "0.12"}
    updated, touched = lines.apply(data, upstream, changes)
    assert touched == ["0.12"]
    assert (
        updated.revision == revision
    )  # a new patch is a new version by itself
    assert {
        line: spec for line, spec in updated.lines.items() if line != "0.12"
    } == others
    assert updated.lines["0.12"].official.version == "v0.12.99"
    assert set(updated.lines["0.12"].official.sha256) == set(PLATFORMS)
    versions = {w.version for w in lines.wheels(updated) if w.line == "0.12"}
    assert f"0.12.99.{revision}" in versions


def test_update_keeps_deliberately_skipped_builds_out():
    data = lines.load_line_json()
    upstream = _upstream(["v0.10.99"], [])
    data, _ = lines.apply(
        data, upstream, [lines.Change("0.10", "official", "v0.10.99")]
    )
    assert "linux-arm64" not in data.lines["0.10"].official.sha256
    assert "linux-arm64" in data.lines["0.10"].official.skip


def test_update_adds_a_new_release_line():
    data = lines.load_line_json()
    upstream = _upstream(["v0.99.0"], [])
    changes = [c for c in lines.plan(data, upstream) if c.line == "0.99"]
    assert changes == [lines.Change("0.99", "official", "v0.99.0")]
    data, _ = lines.apply(data, upstream, changes)
    assert (
        lines.find("0.99", LINUX.wheel_tag, data).version
        == f"0.99.0.{data.revision}"
    )


def test_a_release_without_published_checksums_stops_the_update(monkeypatch):
    data = lines.load_line_json()
    upstream: lines.Upstream = {
        "official": {
            "v0.12.99": _release("v0.12.99", list(PLATFORMS), digests=False)
        }
    }
    with pytest.raises(ReleaseToolError, match="no published checksum"):
        lines.apply(
            data, upstream, [lines.Change("0.12", "official", "v0.12.99")]
        )


def test_a_platform_missing_from_a_release_is_a_warning(capsys):
    data = lines.load_line_json()
    without_windows_arm = [key for key in PLATFORMS if key != "windows-arm64"]
    upstream: lines.Upstream = {
        "official": {"v0.12.99": _release("v0.12.99", without_windows_arm)}
    }
    data, _ = lines.apply(
        data, upstream, [lines.Change("0.12", "official", "v0.12.99")]
    )
    assert "windows-arm64" not in data.lines["0.12"].official.sha256
    assert (
        "warning: v0.12.99 has no nvim-win-arm64.zip"
        in capsys.readouterr().err
    )


def test_a_failed_github_request_is_a_clean_error(monkeypatch, capsys):
    def cut_off(repo):
        raise http.client.IncompleteRead(b"[", 100)

    monkeypatch.setattr(lines, "neovim_releases", cut_off)
    with pytest.raises(SystemExit) as excinfo:
        main(["lines", "update", "--dry-run"])
    assert excinfo.value.code == 1
    assert (
        "release_tool: error: cannot ask GitHub: IncompleteRead"
        in capsys.readouterr().err
    )


def test_a_dry_run_reports_the_update_and_writes_nothing(
    tmp_path, monkeypatch, capsys
):
    path = tmp_path / "lines.json"
    lines.save_line_json(lines.load_line_json(), path)
    before = path.read_bytes()
    data = lines.load_line_json(path)
    current = [spec.official.version for spec in data.lines.values()]
    old = [s.old_glibc.version for s in data.lines.values() if s.old_glibc]
    upstream = _upstream([*current, "v0.12.99"], old)  # Neovim ships 0.12.99
    by_repo = {repo: upstream[kind] for kind, repo in lines.REPOS.items()}
    monkeypatch.setattr(lines, "neovim_releases", by_repo.__getitem__)
    monkeypatch.setattr(
        lines, "ensure_license", lambda *a: pytest.fail("wrote a license")
    )

    main(["lines", "--file", str(path), "update", "--dry-run"])
    out = capsys.readouterr().out
    assert f"0.12 -> 0.12.99.{data.revision} (official v0.12.99)" in out
    assert "dry run: lines.json not written" in out
    assert path.read_bytes() == before


def test_bump_raises_the_revision_of_every_line(tmp_path):
    path = tmp_path / "lines.json"
    lines.save_line_json(lines.load_line_json(), path)
    before = lines.load_line_json(path)
    main(["lines", "--file", str(path), "bump"])
    after = lines.load_line_json(path)
    assert after.revision == before.revision + 1
    assert after.lines == before.lines
    assert {w.version.rsplit(".", 1)[1] for w in lines.wheels(after)} == {
        str(after.revision)
    }


def test_lagging_old_glibc_builds_are_a_note():
    data = with_pin(
        lines.load_line_json(), "0.12", "old_glibc", version="v0.12.1"
    )
    assert any(note.startswith("0.12:") for note in lines.lagging(data))


# --------------------------------------------------------------------------
# release notes
# --------------------------------------------------------------------------
def test_release_notes_list_each_wheels_neovim(tmp_path):
    data = with_pin(
        lines.load_line_json(),
        "0.12",
        "old_glibc",
        version=lines.load_line_json().lines["0.12"].official.version,
    )
    official = fake_wheel(tmp_path, lines.find("0.12", LINUX.wheel_tag, data))
    old = fake_wheel(tmp_path, lines.find("0.12", OLD_X86, data))
    notes = release_notes.notes([official, old])
    (version,) = notes
    neovim = version.rsplit(".", 1)[0]
    assert f'pip install "nvim-bin=={neovim}.*"' in notes[version]
    assert '"nvim-bin==0.12.*"' in notes[version]
    assert LINUX.wheel_tag in notes[version] and OLD_X86 in notes[version]


def test_a_lagging_old_glibc_build_is_its_own_version():
    data = with_pin(
        lines.load_line_json(), "0.12", "official", version="v0.12.6"
    )
    data = with_pin(data, "0.12", "old_glibc", version="v0.12.5")
    revision = data.revision
    assert (
        lines.find("0.12", LINUX.wheel_tag, data).version
        == f"0.12.6.{revision}"
    )
    assert lines.find("0.12", OLD_X86, data).version == f"0.12.5.{revision}"
