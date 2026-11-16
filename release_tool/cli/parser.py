from __future__ import annotations

import argparse

from release_tool.cli.commands import (
    build,
    check,
    fetch,
    lines,
    release_notes,
    smoke,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="release_tool",
        description="Development tooling for nvim-bin.",
    )

    subparsers = parser.add_subparsers(
        dest="command", required=True, metavar="COMMAND"
    )

    lines.register(subparsers)
    fetch.register(subparsers)
    build.register(subparsers)
    check.register(subparsers)
    smoke.register(subparsers)
    release_notes.register(subparsers)

    return parser
