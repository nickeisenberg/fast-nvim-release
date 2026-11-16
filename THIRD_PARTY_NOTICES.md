# Third-party notices

## Neovim

This package redistributes unmodified builds of
[Neovim](https://github.com/neovim/neovim) from its official GitHub releases,
and, in the wheels for older Linux systems, from
[`neovim/neovim-releases`](https://github.com/neovim/neovim-releases), where the
Neovim project publishes builds for glibc 2.17.

The Neovim lines shipped, and the exact builds, are pinned in `lines.json`;
each wheel also records its build in `nvim_bin/_nvim/build.json`.

* Neovim: <https://github.com/neovim/neovim>
* License: Apache License 2.0, except for parts contributed under the Vim
  license. Each wheel ships the full text for its line as
  `nvim_bin/_nvim/LICENSE.nvim-<line>.txt` (taken from the tag of the
  line's official build when the line is added), and the Vim license is also
  present in the runtime as `share/nvim/runtime/doc/uganda.txt`.
* Each vendored build is extracted from the release archive whose sha256 is
  pinned in `lines.json`.

## Bundled third-party components

Neovim's release archives include third-party code and assets (tree-sitter
parsers, Lua libraries, syntax files and colorschemes, and on Windows the
`libuv`/`LuaJIT` DLLs and the `win32yank`, `xxd` and `tee` helper executables).
Their licenses are documented by the Neovim project in the `doc/` tree of the
runtime and in the upstream repositories of those components.

No changes are made to the vendored binaries: `python -m release_tool fetch` extracts
`bin/`, `lib/nvim/` and `share/nvim/runtime/` from each published archive.

## nvim-bin itself

MIT licensed; see `LICENSE`.
