# Contributing to nvim-bin

## Getting started

```console
$ git clone git@github.com:nickeisenberg/nvim-bin.git
$ cd nvim-bin
$ python -m pip install -r requirements-ci.txt   # the pinned tools CI uses
$ python -m pytest                 # the whole suite, offline, fake Neovim builds
$ python -m ruff check .
$ python -m ruff format --check .
```

`requirements-ci.txt` is the one list of tool pins for the workflows. Actions
are pinned to commit SHAs with the tag in a comment; bump both together.

## Layout

```
lines.json                          the revision, and every Neovim line's pinned builds
src/nvim_bin/cli.py                 setup / path / run  (the stable interface)
src/nvim_bin/bundle.py              the bundled build, installing into a prefix
src/nvim_bin/platforms.py           platforms, wheel tags, which machine this is
setup.py                            versions and tags the wheel from _nvim/build.json
release_tool/                       dev tooling, never in a wheel: python -m release_tool --help
  gh_client.py                      GitHub: Neovim's release listings, and downloads
  cli/main.py, cli/parser.py        the entry point, and every command registered
  cli/commands/lines.py             lines.json and the wheels it describes; lines update / bump
  cli/commands/fetch.py             download, verify and unpack one wheel's build
  cli/commands/build.py             build any set of wheels (or only unpublished ones)
  cli/commands/check.py             validate wheels against lines.json
  cli/commands/smoke.py             install wheels in a clean venv and use them
  cli/commands/release_notes.py     one GitHub release per published version
```

## Versions and lines

A package version is `<Neovim version>.<revision>` (`0.12.6.1`): the exact
Neovim inside, and the `revision` of this project's code from `lines.json`.
Each line carries the newest patch of its Neovim line: the official build
(`neovim/neovim`) for six platforms, plus, where Neovim publishes one, the
glibc 2.17 build (`neovim/neovim-releases`) for older Linux, in wheels tagged
`manylinux_2_17_*.manylinux2014_*`. pip prefers the `manylinux_2_34` wheel
wherever it can run.

A new Neovim patch is a new version by itself; the revision stays. The
revision goes up only when the installed code changes, and then every line's
current patch is released again. Older patches are never rebuilt. When the
old-glibc build lags (official v0.12.6, old-glibc v0.12.5) its wheels are their
own version (`0.12.5.1`); once it catches up, its wheels are added to the
existing `0.12.6.1`. Nothing is ever deleted from PyPI, and a release only
uploads the wheel files the index does not have yet.

The command line is a promise (see "Stability" in the README): versions name
Neovim versions, so they cannot signal breaking changes. Add, never remove.

## Building wheels locally

```console
$ python -m release_tool fetch                  # newest line, the wheel pip picks here
$ python -m release_tool fetch --line 0.11      # another line
$ pip install .                                 # or: python -m build --wheel
$ python -m release_tool build --wheel host     # every line's wheel for this machine
$ python -m release_tool build --all            # every wheel of every line
$ python -m release_tool check                  # validate dist/*.whl
$ python -m release_tool smoke dist/*.whl       # install them and use them
```

`fetch` unpacks one build into `src/nvim_bin/_nvim/` (git
ignores it) with a `build.json` naming the version, Neovim build and wheel tag,
which `setup.py` reads. On a Linux older than glibc 2.34 the default is the
old-glibc wheel, so the same two commands work there. Archives are cached in
`~/.cache/nvim-bin` (`NVIM_BIN_CACHE_DIR`) and checked against
`lines.json` every time. The `LICENSE.nvim-<line>.txt` files in `_nvim/` are
tracked, because every wheel must ship its line's license (and only that one).

## When Neovim ships a patch (or a new line)

`.github/workflows/upstream.yml` runs `python -m release_tool lines update`
every Monday. It moves each pin to the newest patch in its repository with the
checksums GitHub publishes and adds new lines (the revision stays), and opens
a pull request (branch `pins/update`) with CI started on it. Review
`lines.json`, wait for CI, merge, then release.

By hand: `python -m release_tool lines update --dry-run` shows what would
move and writes nothing; `python -m release_tool lines update` edits
`lines.json` the same way the workflow does.

A new line's builds are checked against their tags: `check` fails
if an executable is built for another CPU, or needs a newer glibc or macOS
than its tag promises. A build that cannot meet its tag goes in that build's
`skip` list in `lines.json` (0.10's arm64 build needs glibc 2.38, for
example). If a release lacks a platform's archive, `lines update` warns and
that platform gets no wheel for the patch; the pins PR shows it as a removed
checksum.

The README's platform tables are written by hand: update them when a line is
added or a platform changes.

## When our code changes

Changes to what ends up in a wheel need a release: the installed package
(`src/nvim_bin/`), and the metadata from `pyproject.toml`,
`setup.py` and `README.md` (the PyPI page). `release_tool/`, CI and other docs
do not. Batch fixes, then:

```console
$ python -m release_tool lines bump    # revision + 1: every line's current patch
```

commit, merge, and release.

## Releasing

Actions > release > Run workflow, choosing the index (TestPyPI by default).
It builds every wheel in `lines.json` that index does not have, validates and
smoke-tests them, and uploads them. Publishing to PyPI also creates one GitHub
release per version (or adds to it, when wheels join an existing version),
listing the exact Neovim in each wheel. An index never accepts the same file
twice; rehearse on TestPyPI first. The workflow refuses to run from any
branch but master. Only wheels are published, never an sdist: pip on an older
Linux relies on skipping versions without a wheel for it.

If only the GitHub release job fails, re-run the failed jobs of that run (its
wheels artifact lasts 14 days); a new run would find nothing to publish and
skip the release notes.

One-time setup on each index (Your account > Publishing > add a pending
publisher, or the project's Publishing settings): repository
`nickeisenberg/nvim-bin`, workflow `release.yml`, environment
`testpypi` or `pypi` (matching the GitHub environments of the same names). The
weekly bot also needs Settings > Actions > General > "Allow GitHub Actions to
create and approve pull requests".

## License

By contributing you agree your changes are MIT licensed, like the rest of the
project. Vendored Neovim binaries keep their own licenses; see
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
