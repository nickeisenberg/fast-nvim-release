from __future__ import annotations

import sys
from typing import List, Optional

from release_tool.cli.parser import build_parser
from release_tool.exceptions import ReleaseToolError


def main(argv: Optional[List[str]] = None) -> None:
    parser = build_parser()

    args = parser.parse_args(argv)

    try:
        args.func(args)
    except ReleaseToolError as e:
        print(f"release_tool: error: {e}", file=sys.stderr)
        sys.exit(1)
