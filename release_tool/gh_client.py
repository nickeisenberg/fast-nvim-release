"""GitHub, as far as nvim-bin needs it: release listings and downloads.

Everything the scripts download from GitHub goes through here: the release
listings of Neovim's repositories (the API), and files on github.com (release
archives, their ``.sha256sum`` files, Neovim's license).  It knows GitHub, not
Neovim: which tags are Neovim releases is for the ``lines`` command to decide.  Set
``GITHUB_TOKEN`` to lift the API's anonymous rate limit.
"""

from __future__ import annotations

import json
import os
import shutil
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

API = "https://api.github.com/repos/{}/releases?per_page=100&page={}"
USER_AGENT = "nvim-bin"
#: 500 releases; Neovim's repositories keep far fewer, newest first
MAX_PAGES = 5


# --------------------------------------------------------------------------
# releases
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Asset:
    """A file attached to a release, e.g. ``nvim-linux-x86_64.tar.gz``."""

    name: str
    sha256: Optional[str]  #: from GitHub's ``digest``, when it published one
    url: str  #: ``browser_download_url``

    @classmethod
    def from_dict(cls, data: dict) -> Asset:
        algorithm, _, value = (data.get("digest") or "").partition(":")
        return cls(
            name=data["name"],
            sha256=value if algorithm == "sha256" else None,
            url=data["browser_download_url"],
        )


@dataclass(frozen=True)
class Release:
    """One release of a repository, e.g. Neovim's ``v0.12.6``."""

    tag: str  #: the git tag the release belongs to
    stable: bool  #: not a draft or a prerelease
    assets: Dict[str, Asset]  #: file name -> asset

    @classmethod
    def from_dict(cls, data: dict) -> Release:
        assets = (Asset.from_dict(asset) for asset in data.get("assets", []))
        return cls(
            tag=data["tag_name"],
            stable=not (data["draft"] or data["prerelease"]),
            assets={asset.name: asset for asset in assets},
        )


def releases(repo: str) -> Dict[str, Release]:
    """tag -> release, for every stable release of ``repo``."""
    found: Dict[str, Release] = {}
    for page in range(1, MAX_PAGES + 1):
        batch = json.loads(download_bytes(API.format(repo, page)))
        for raw in batch:
            release = Release.from_dict(raw)
            if release.stable:
                found[release.tag] = release
        if len(batch) < 100:  # a page that isn't full is the last one
            break
    return found


# --------------------------------------------------------------------------
# downloads
# --------------------------------------------------------------------------
def _request(url: str) -> urllib.request.Request:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    token = os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        # unredirected: release downloads redirect to another host
        request.add_unredirected_header("Authorization", f"Bearer {token}")
    return request


def download_bytes(url: str) -> bytes:
    """A small file (an API page, a ``.sha256sum``, a license), in memory."""
    with urllib.request.urlopen(_request(url), timeout=30) as response:
        return response.read()


def download_file(url: str, dest: Path) -> None:
    """A large file (a release archive), streamed to ``dest``.

    Written to ``dest.part`` first and renamed at the end, so a cut-off
    download never looks like a complete one.
    """
    partial = dest.with_name(dest.name + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with (
        urllib.request.urlopen(_request(url), timeout=60) as response,
        partial.open("wb") as out,
    ):
        shutil.copyfileobj(response, out)
    os.replace(partial, dest)
