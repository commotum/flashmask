---
title: "uv"
source: "https://docs.astral.sh/uv/"
author:
  - "[[charliermarsh]]"
published: 2026-01-15
created: 2026-03-05
description: "uv is an extremely fast Python package and project manager, written in Rust."
tags:
  - "clippings"
---
[Skip to content](https://docs.astral.sh/uv/#uv)

An extremely fast Python package and project manager, written in Rust.

![Shows a bar chart with benchmark results.](https://github.com/astral-sh/uv/assets/1309177/629e59c0-9c6e-4013-9ad4-adb2bcf5080d#only-light)

![Shows a bar chart with benchmark results.](https://github.com/astral-sh/uv/assets/1309177/03aa9163-1c79-4a87-a31d-7a9311ed9310#only-dark)

*Installing [Trio](https://trio.readthedocs.io/) 's dependencies with a warm cache.*

## Highlights

- A single tool to replace `pip`, `pip-tools`, `pipx`, `poetry`, `pyenv`, `twine`, `virtualenv`, and more.
- [10-100x faster](https://github.com/astral-sh/uv/blob/main/BENCHMARKS.md) than `pip`.
- Provides [comprehensive project management](https://docs.astral.sh/uv/#projects), with a [universal lockfile](https://docs.astral.sh/uv/concepts/projects/layout/#the-lockfile).
- [Runs scripts](https://docs.astral.sh/uv/#scripts), with support for [inline dependency metadata](https://docs.astral.sh/uv/guides/scripts/#declaring-script-dependencies).
- [Installs and manages](https://docs.astral.sh/uv/#python-versions) Python versions.
- [Runs and installs](https://docs.astral.sh/uv/#tools) tools published as Python packages.
- Includes a [pip-compatible interface](https://docs.astral.sh/uv/#the-pip-interface) for a performance boost with a familiar CLI.
- Supports Cargo-style [workspaces](https://docs.astral.sh/uv/concepts/projects/workspaces/) for scalable projects.
- Disk-space efficient, with a [global cache](https://docs.astral.sh/uv/concepts/cache/) for dependency deduplication.
- Installable without Rust or Python via `curl` or `pip`.
- Supports macOS, Linux, and Windows.

uv is backed by [Astral](https://astral.sh/), the creators of [Ruff](https://github.com/astral-sh/ruff).

## Installation

Install uv with our official standalone installer:

```js
$ curl -LsSf https://astral.sh/uv/install.sh | sh
```

```js
PS> powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Then, check out the [first steps](https://docs.astral.sh/uv/getting-started/first-steps/) or read on for a brief overview.

Tip

uv may also be installed with pip, Homebrew, and more. See all of the methods on the [installation page](https://docs.astral.sh/uv/getting-started/installation/).

## Projects

uv manages project dependencies and environments, with support for lockfiles, workspaces, and more, similar to `rye` or `poetry`:

```js
$ uv init example
Initialized project \`example\` at \`/home/user/example\`

$ cd example

$ uv add ruff
Creating virtual environment at: .venv
Resolved 2 packages in 170ms
   Built example @ file:///home/user/example
Prepared 2 packages in 627ms
Installed 2 packages in 1ms
 + example==0.1.0 (from file:///home/user/example)
 + ruff==0.5.4

$ uv run ruff check
All checks passed!

$ uv lock
Resolved 2 packages in 0.33ms

$ uv sync
Resolved 2 packages in 0.70ms
Audited 1 package in 0.02ms
```

See the [project guide](https://docs.astral.sh/uv/guides/projects/) to get started.

uv also supports building and publishing projects, even if they're not managed with uv. See the [packaging guide](https://docs.astral.sh/uv/guides/package/) to learn more.

## Scripts

uv manages dependencies and environments for single-file scripts.

Create a new script and add inline metadata declaring its dependencies:

```js
$ echo 'import requests; print(requests.get("https://astral.sh"))' > example.py

$ uv add --script example.py requests
Updated \`example.py\`
```

Then, run the script in an isolated virtual environment:

```js
$ uv run example.py
Reading inline script metadata from: example.py
Installed 5 packages in 12ms
<Response [200]>
```

See the [scripts guide](https://docs.astral.sh/uv/guides/scripts/) to get started.

## Tools

uv executes and installs command-line tools provided by Python packages, similar to `pipx`.

Run a tool in an ephemeral environment using `uvx` (an alias for `uv tool run`):

```js
$ uvx pycowsay 'hello world!'
Resolved 1 package in 167ms
Installed 1 package in 9ms
 + pycowsay==0.0.0.2
  """

  ------------
< hello world! >
  ------------
   \   ^__^
    \  (oo)\_______
       (__)\       )\/\
           ||----w |
           ||     ||
```

Install a tool with `uv tool install`:

```js
$ uv tool install ruff
Resolved 1 package in 6ms
Installed 1 package in 2ms
 + ruff==0.5.4
Installed 1 executable: ruff

$ ruff --version
ruff 0.5.4
```

See the [tools guide](https://docs.astral.sh/uv/guides/tools/) to get started.

## Python versions

uv installs Python and allows quickly switching between versions.

Install multiple Python versions:

```js
$ uv python install 3.10 3.11 3.12
Searching for Python versions matching: Python 3.10
Searching for Python versions matching: Python 3.11
Searching for Python versions matching: Python 3.12
Installed 3 versions in 3.42s
 + cpython-3.10.14-macos-aarch64-none
 + cpython-3.11.9-macos-aarch64-none
 + cpython-3.12.4-macos-aarch64-none
```

Download Python versions as needed:

Use a specific Python version in the current directory:

```js
$ uv python pin 3.11
Pinned \`.python-version\` to \`3.11\`
```

See the [installing Python guide](https://docs.astral.sh/uv/guides/install-python/) to get started.

## The pip interface

uv provides a drop-in replacement for common `pip`, `pip-tools`, and `virtualenv` commands.

uv extends their interfaces with advanced features, such as dependency version overrides, platform-independent resolutions, reproducible resolutions, alternative resolution strategies, and more.

Migrate to uv without changing your existing workflows — and experience a 10-100x speedup — with the `uv pip` interface.

Compile requirements into a platform-independent requirements file:

```js
$ uv pip compile docs/requirements.in \
   --universal \
   --output-file docs/requirements.txt
Resolved 43 packages in 12ms
```

Create a virtual environment:

```js
$ uv venv
Using CPython 3.12.3
Creating virtual environment at: .venv
Activate with: source .venv/bin/activate
```

Install the locked requirements:

```js
$ uv pip sync docs/requirements.txt
Resolved 43 packages in 11ms
Installed 43 packages in 208ms
 + babel==2.15.0
 + black==24.4.2
 + certifi==2024.7.4
 ...
```

See the [pip interface documentation](https://docs.astral.sh/uv/pip/) to get started.

## Learn more

See the [first steps](https://docs.astral.sh/uv/getting-started/first-steps/) or jump straight to the [guides](https://docs.astral.sh/uv/guides/) to start using uv.