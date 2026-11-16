from __future__ import annotations


class ReleaseToolError(Exception):
    """Something went wrong that the user can act on; reported without a traceback."""
