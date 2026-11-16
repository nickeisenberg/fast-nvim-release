from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from release_tool.commands import (
    build,
    check,
    fetch,
    lines,
    release_notes,
    smoke,
)
from release_tool.exceptions import ReleaseToolError


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


def main(argv: Optional[List[str]] = None) -> None:
    parser = build_parser()

    args = parser.parse_args(argv)

    try:
        args.func(args)
    except ReleaseToolError as e:
        print(f"release_tool: error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
