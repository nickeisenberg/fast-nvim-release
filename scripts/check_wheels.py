#!/usr/bin/env python3
"""Validate built wheels against ``lines.json`` before they are uploaded.

    python scripts/check_wheels.py [dist/*.whl]

A wheel ``nvim_bin-<neovim>.<revision>-py3-none-<tag>.whl`` must carry
exactly the build ``lines.json`` pins for that line and tag, that Neovim
version and the current revision (``build.json`` says so),
with its executable, a complete runtime and its line's Neovim license.  The
executable must be built for the tag's CPU, and its glibc (Linux) or macOS
requirement must fit the tag, or pip would install a wheel that cannot start.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
import zipfile
from pathlib import Path
from typing import List, Optional, Tuple, cast

import lines

from nvim_bin.platforms import WheelTag

WHEEL_NAME = re.compile(
    r"^nvim_bin-(?P<line>\d+\.\d+)\.(?P<patch>\d+)\.(?P<revision>\d+)"
    r"-py3-none-(?P<tag>.+)\.whl$"
)
MANYLINUX = re.compile(r"^manylinux_(\d+)_(\d+)_")
MACOSX = re.compile(r"^macosx_(\d+)_(\d+)_")
GLIBC_SYMBOL = re.compile(rb"GLIBC_(\d+)\.(\d+)")
#: (format, machine code) -> CPU, as the platform keys name it
MACHINES = {
    ("elf", 0x3E): "x86_64",
    ("elf", 0xB7): "arm64",
    ("macho", 0x01000007): "x86_64",
    ("macho", 0x0100000C): "arm64",
    ("pe", 0x8664): "x86_64",
    ("pe", 0xAA64): "arm64",
}
PAYLOAD = "nvim_bin/_nvim/"
PYPI_MAX_BYTES = 100 * 1024 * 1024
#: 0.10 ships ~1900 runtime files, 0.12 ~2100; far fewer means the runtime is missing
MIN_RUNTIME_FILES = 1500


def glibc_floor(binary: bytes) -> Optional[Tuple[int, int]]:
    """The newest ``GLIBC_x.y`` symbol version an ELF binary requires."""
    found = {(int(a), int(b)) for a, b in GLIBC_SYMBOL.findall(binary)}
    return max(found) if found else None


def cpu(binary: bytes) -> Optional[str]:
    """The CPU a little-endian ELF, thin 64-bit Mach-O or PE executable is built for."""
    try:
        if binary[:4] == b"\x7fELF":
            return MACHINES.get(
                ("elf", struct.unpack_from("<H", binary, 18)[0])
            )
        if binary[:4] == b"\xcf\xfa\xed\xfe":
            return MACHINES.get(
                ("macho", struct.unpack_from("<I", binary, 4)[0])
            )
        if binary[:2] == b"MZ":
            offset = struct.unpack_from("<I", binary, 0x3C)[0]
            if binary[offset : offset + 4] == b"PE\0\0":
                return MACHINES.get(
                    ("pe", struct.unpack_from("<H", binary, offset + 4)[0])
                )
    except struct.error:
        pass
    return None


def macos_floor(binary: bytes) -> Optional[Tuple[int, int]]:
    """The minimum macOS a thin 64-bit Mach-O executable declares."""
    try:
        if binary[:4] != b"\xcf\xfa\xed\xfe":
            return None
        ncmds = struct.unpack_from("<I", binary, 16)[0]
        offset = 32  # the 64-bit header
        for _ in range(ncmds):
            cmd, size = struct.unpack_from("<II", binary, offset)
            if cmd == 0x32:  # LC_BUILD_VERSION: platform, then minos
                version = struct.unpack_from("<I", binary, offset + 12)[0]
            elif cmd == 0x24:  # LC_VERSION_MIN_MACOSX
                version = struct.unpack_from("<I", binary, offset + 8)[0]
            else:
                offset += size
                continue
            return (version >> 16, (version >> 8) & 0xFF)
    except struct.error:
        pass
    return None


def check(path: Path, data: Optional[lines.LineJson] = None) -> List[str]:
    data = data or lines.load_line_json()
    match = WHEEL_NAME.match(path.name)
    if not match:
        return [f"unexpected wheel name: {path.name}"]
    line, tag = match.group("line"), cast(WheelTag, match.group("tag"))
    version = f"{line}.{match.group('patch')}.{match.group('revision')}"
    if line not in data.lines:
        return [f"{line} is not a line in lines.json"]
    try:
        spec = lines.find(line, tag, data)
    except KeyError as exc:
        return [exc.args[0]]
    if version != spec.version:
        return [
            f"version {version}, but lines.json says {spec.version} for {tag}"
        ]

    problems = []
    with zipfile.ZipFile(path) as wheel:
        names = set(wheel.namelist())
        info_name = f"{PAYLOAD}build.json"
        info = json.loads(wheel.read(info_name)) if info_name in names else {}
        expected = {
            "version": spec.version,
            "line": line,
            "neovim": spec.build.version,
            "platform": spec.platform.key,
            "tag": tag,
        }
        if info != expected:
            problems.append(f"build.json is {info}, expected {expected}")
        exe = f"{PAYLOAD}bin/{spec.platform.exe}"
        runtime = sum(
            1
            for name in names
            if name.startswith(f"{PAYLOAD}share/nvim/runtime/")
        )
        if runtime < MIN_RUNTIME_FILES:
            problems.append(f"only {runtime} runtime files")
        license_file = f"{PAYLOAD}{lines.license_name(line)}"
        if license_file not in names:
            problems.append(f"missing {license_file}")
        others = sorted(
            n
            for n in names
            if n.startswith(f"{PAYLOAD}LICENSE.nvim-") and n != license_file
        )
        if others:
            problems.append(
                f"carries other lines' licenses: {', '.join(others)}"
            )
        if exe not in names:
            problems.append(f"missing {exe}")
        else:
            mode = wheel.getinfo(exe).external_attr >> 16
            if not exe.endswith(".exe") and mode and not mode & 0o111:
                problems.append(f"{exe} is not executable")
            binary = wheel.read(exe)
            arch = spec.platform.key.rsplit("-", 1)[1]
            built_for = cpu(binary)
            if built_for != arch:
                problems.append(
                    f"{exe} is built for {built_for or 'an unknown CPU'}, not {arch}"
                )
            manylinux = MANYLINUX.match(tag)
            if manylinux:
                promised = (int(manylinux.group(1)), int(manylinux.group(2)))
                needed = glibc_floor(binary)
                if needed is None or needed > promised:
                    problems.append(
                        f"{exe} needs glibc {needed}, the tag promises {promised}"
                    )
            macosx = MACOSX.match(tag)
            if macosx:
                promised = (int(macosx.group(1)), int(macosx.group(2)))
                needed = macos_floor(binary)
                if needed is None or needed > promised:
                    problems.append(
                        f"{exe} needs macOS {needed}, the tag promises {promised}"
                    )

        meta = next((n for n in names if n.endswith(".dist-info/WHEEL")), None)
        text = wheel.read(meta).decode() if meta else ""
        tags_ok = all(
            f"Tag: py3-none-{part}" in text for part in tag.split(".")
        )
        if "Root-Is-Purelib: false" not in text or not tags_ok:
            problems.append(
                "WHEEL metadata is not a py3-none platform wheel for this tag"
            )
        if not any(n.endswith(".dist-info/entry_points.txt") for n in names):
            problems.append("missing the console script entry point")

    if path.stat().st_size > PYPI_MAX_BYTES:
        problems.append("larger than PyPI's 100 MiB per-file limit")
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "wheels", nargs="*", type=Path, help="default: dist/*.whl"
    )
    args = parser.parse_args(argv)
    wheels = args.wheels or sorted((lines.REPOROOT / "dist").glob("*.whl"))
    if not wheels:
        parser.error("no wheels given and none in dist/")
    data = lines.load_line_json()
    failed = 0
    for wheel in wheels:
        problems = check(wheel, data)
        print(f"{wheel.name}: {'FAILED' if problems else 'OK'}")
        for problem in problems:
            print(f"  - {problem}")
        failed += bool(problems)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
