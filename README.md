# fast-nvim-release

Neovim from PyPI, in seconds, without root: pick a Neovim line with pip and get
the official build for Linux (including RHEL 8-era systems), macOS or Windows,
as a plain directory you put where you want. Made for CI, containers, fresh
servers and machines without root, and just as handy on your own machine.

```console
$ uvx fast-nvim-release setup --prefix ~/.local
installed Neovim v0.12.6 into /home/you/.local
  /home/you/.local/bin/nvim
```

`setup` takes about 0.15 seconds; creating a venv and installing the wheel
(10-13 MB, the binaries are inside) takes a few seconds more. Nothing is
downloaded from GitHub, at install time or later.

## Install

```console
$ pip install "fast-nvim-release==0.12.*"     # the newest Neovim 0.12
$ pip install "fast-nvim-release==0.11.7.*"   # exactly Neovim v0.11.7
$ fast-nvim-release setup --prefix ~/.local
installed Neovim v0.12.6 into /home/you/.local
  /home/you/.local/bin/nvim
```

`pipx install` and `uv tool install` work the same way, and so does uvx in one
command: `uvx --from "fast-nvim-release==0.11.*" fast-nvim-release setup --prefix ~/.local`.

The package version is the Neovim version plus a revision of this tool
(`0.12.6.1` is Neovim v0.12.6). Keep the trailing `.*`, so you get the newest
revision; see [How versions work](#how-versions-work).

## Use cases

**Testing a plugin in CI against several Neovim versions.** This is what it was
built for, in the CI of [iron.nvim](https://github.com/Vigemus/iron.nvim). The
test command is an example; use your plugin's own.

```yaml
jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        nvim: ["0.10", "0.11", "0.12"]  # quoted, or YAML reads 0.10 as 0.1
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install "fast-nvim-release==${{ matrix.nvim }}.*"
      - run: fast-nvim-release setup --prefix ~/.local && echo "$HOME/.local/bin" >> "$GITHUB_PATH"
      - run: nvim --headless -c "PlenaryBustedDirectory tests"
```

Nothing here is specific to GitHub Actions: the same commands work in GitLab
CI, Jenkins or anywhere else with a Python. `fast-nvim-release run [ARGS]`
starts the bundled Neovim without the `setup` step.

**Containers.** In a glibc-based image (Debian, Ubuntu, `python:*-slim`, RHEL
and the like) you are usually root, so install straight into `/usr/local`:

```console
$ docker exec -it mycontainer bash
# pip install fast-nvim-release
# fast-nvim-release setup --prefix /usr/local
```

Images without Python need it first (`apt-get install python3-pip`; Debian's
and Ubuntu's own pip then wants a venv, uvx, or `--break-system-packages`).
Alpine has no Neovim build to ship (see [Platforms](#platforms)); use
`apk add neovim` there. Plugin setups often also want `git`, a C compiler for
Tree-sitter parsers and `ripgrep`, which slim images do not include.

**No root or sudo.** Install for your user and put Neovim in `~/.local/bin`:

```console
$ python3 -m venv ~/.venvs/nvim && ~/.venvs/nvim/bin/pip install -q fast-nvim-release
$ ~/.venvs/nvim/bin/fast-nvim-release setup --prefix ~/.local
```

**Older Linux and HPC clusters.** The same commands work on RHEL 8 and similar
systems: pip picks a wheel with Neovim's glibc 2.17 build by itself. It needs
Python 3.9 or newer; on RHEL 8 that is `python3.9` (the `python39` package) or
a module your site provides.

**Your own machine, your own layout.** Each `setup` gives you a complete,
self-contained Neovim tree, so you can keep versions side by side and decide
yourself which one is `nvim`:

```console
$ uvx --from "fast-nvim-release==0.11.*" fast-nvim-release setup --prefix ~/opt/nvim-0.11
$ uvx --from "fast-nvim-release==0.12.*" fast-nvim-release setup --prefix ~/opt/nvim-0.12
$ ln -sf ~/opt/nvim-0.12/bin/nvim ~/.local/bin/nvim
```

Nothing updates behind your back: run `setup` again when you want a newer
patch.

**Restricted or offline networks.** Only PyPI, or an internal PyPI mirror, is
needed. For machines with no network at all, see
[Installing for other machines](#installing-for-other-machines).

## Platforms

| Line | Linux x86_64 | Linux arm64 | Older Linux x86_64 | Older Linux arm64 | macOS (Intel, Apple silicon) | Windows x64 | Windows arm64 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `0.10` | ✓ | — ¹ | ✓ | — | ✓ | ✓ | — ² |
| `0.11` | ✓ | ✓ | ✓ | — | ✓ | ✓ | ✓ |
| `0.12` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

"Linux" means glibc 2.34 or newer (Neovim's official builds); "Older Linux"
means glibc 2.17 to 2.33 (RHEL 8, CentOS 7), with the builds Neovim publishes
in [`neovim/neovim-releases`](https://github.com/neovim/neovim-releases). You
never choose between them: pip installs the right one. macOS needs 11 or newer.

¹ Neovim's 0.10 arm64 build needs glibc 2.38, more than the other Linux builds.
² Neovim published no Windows arm64 build of 0.10.

Everything else gets no wheel ("no matching distribution"): Alpine and other
musl systems, macOS before 11, armv7, and Linux arm64 with a glibc too old for
the lines marked above. pip picks the wheel from the running Python, so an
x86_64 Python under Rosetta 2 (or on Windows on ARM) gets the x86_64 build,
which works but runs emulated.

## Where the binaries come from

The wheels carry Neovim's own release builds, unmodified: the archives
published in [`neovim/neovim`](https://github.com/neovim/neovim/releases) and,
for older Linux, `neovim/neovim-releases`. Each archive's sha256 is pinned in
[`lines.json`](https://github.com/nickeisenberg/fast-nvim-release/blob/master/lines.json)
and checked when the wheel is built. Wheels are built and uploaded only by this
repository's [release workflow](https://github.com/nickeisenberg/fast-nvim-release/blob/master/.github/workflows/release.yml),
with PyPI attestations that tie every file to the workflow run that made it
(shown on each file's page on PyPI). A weekly job proposes new Neovim patches,
with GitHub's checksums, as a pull request.

## How versions work

A version is `<Neovim version>.<revision>`: `0.12.6.1` is Neovim v0.12.6 with
revision 1 of this tool. A new Neovim patch is released as a new version
(`0.12.7.1`). A fix in this tool raises the revision, which releases the newest
patch of every line again (`0.10.4.2`, `0.11.7.2`, `0.12.7.2`); older patches
stay on PyPI as they were. So:

* `==0.12.*` follows the 0.12 line: its newest patch, with the newest fixes;
* `==0.12.6.*` stays on Neovim v0.12.6, with the newest fixes for it.

(pip allows one `*`, at the end; `==0.12.*` already covers every `0.12.Z.K`.)

`neovim-releases` often trails the official releases by a patch. Its wheels
are then released under their own Neovim version (official v0.12.6, older
Linux v0.12.5: `0.12.5.1`), and pip on an older system falls back to that
version when the newest one has no wheel for it. Once `neovim-releases`
catches up, the older-Linux wheels are added to the newer version.

`fast-nvim-release --version`, the
[GitHub releases](https://github.com/nickeisenberg/fast-nvim-release/releases)
and `lines.json` say exactly which Neovim each version carries. There are no
plans to drop a line: every line from 0.10 on keeps getting this tool's fixes.

## Commands

| Command | What it does |
| --- | --- |
| `fast-nvim-release setup --prefix DIR [--force]` | Install the bundled Neovim into `DIR` |
| `fast-nvim-release path [--prefix DIR]` | Print the `nvim` this tool installed in `DIR` |
| `fast-nvim-release run [--] [ARGS]` | Run the bundled nvim without installing it |
| `fast-nvim-release --version` | This tool's version and the Neovim it carries |

`setup` writes only into `--prefix` (there is no default), with an ordinary
Neovim layout:

```console
<prefix>/bin/nvim                     # the executable, which belongs on PATH
<prefix>/lib/nvim/...                 # libraries it needs
<prefix>/share/nvim/runtime/...       # runtime files it needs
<prefix>/share/fast-nvim-release/     # records what was installed here
```

A prefix holds one Neovim: `setup` from another version replaces it, so use
separate prefixes to keep several. Files this tool did not install are never
overwritten without `--force`, and other files in `<prefix>/bin` are left
alone. If `<prefix>/bin` is not on `PATH`, `setup` says so.

`path` exits 1 when the prefix has no install, which makes it the check to use
in scripts; without `--prefix` it looks in `~/.local` (on Windows,
`%LOCALAPPDATA%\fast-nvim-release`). `run` hands everything after it to Neovim
verbatim: `fast-nvim-release run -- -u NONE --headless "+q"`.

**Stability.** Since the version number names the Neovim inside, it cannot
announce breaking changes in this tool. Instead, the command line is stable:
the commands and flags above, the prefix layout and the exit codes (0 success,
1 error, 2 bad usage) keep working. Commands and flags may be added, never
removed or changed in meaning.

## Installing for other machines

Each wheel carries one platform's build. To prepare all of them, for example
for machines without internet access, let pip download the other platforms'
wheels of a line:

```console
for p in manylinux_2_34_x86_64 manylinux_2_34_aarch64 macosx_11_0_x86_64 \
         macosx_11_0_arm64 win_amd64 win_arm64 manylinux2014_x86_64 manylinux2014_aarch64; do
  pip download "fast-nvim-release==0.12.*" --no-deps --only-binary=:all: --platform $p -d wheels/
done
```

On each machine, `pip install --no-index --find-links wheels/ fast-nvim-release`
picks the matching wheel. Without Python on the target, unzip the wheel:
`fast_nvim_release/_nvim/` inside it is a complete Neovim tree.

## Other ways to get Neovim

Package managers update Neovim along with the rest of your system, and
[bob](https://github.com/MordechaiHadad/bob) switches versions for you and
handles nightly builds. fast-nvim-release does neither: you get pinned official
builds from PyPI as plain directories, and you choose what goes on `PATH`.

| Option | Root needed | Downloads from | Good fit |
| --- | --- | --- | --- |
| OS package manager (apt, dnf, Homebrew, winget, scoop, ...) | for apt/dnf | your distribution's or the tool's repositories | your own machine |
| [bob](https://github.com/MordechaiHadad/bob) | no | GitHub | your own machine, switching versions, nightly |
| Official tarball / AppImage | no | GitHub | any machine with GitHub access |
| mise / asdf | no | usually GitHub | if you already manage tools with them |
| Neovim setup actions for GitHub Actions | no | usually GitHub | GitHub Actions only |
| fast-nvim-release | no | PyPI or a PyPI mirror | CI, containers, no root, fresh servers, restricted networks, managing versions yourself |

Use something else for nightly builds or releases older than 0.10, on Alpine
or another platform without a wheel, or when you want updates to arrive with
the rest of your system.

## Contributing

Building the wheels, releasing and following Neovim's releases are described
in [`CONTRIBUTING.md`](https://github.com/nickeisenberg/fast-nvim-release/blob/master/CONTRIBUTING.md).

## License

fast-nvim-release is MIT licensed. The bundled Neovim binaries and runtime are
distributed under Neovim's license (Apache-2.0, plus the Vim license for parts
of the runtime); see
[`THIRD_PARTY_NOTICES.md`](https://github.com/nickeisenberg/fast-nvim-release/blob/master/THIRD_PARTY_NOTICES.md)
and the `fast_nvim_release/_nvim/LICENSE.nvim-<line>.txt` file inside the wheel.
