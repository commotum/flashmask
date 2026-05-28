---
title: "Getting help | uv"
source: "https://docs.astral.sh/uv/getting-started/help/"
author:
  - "[[charliermarsh]]"
published: 2025-09-17
created: 2026-03-05
description: "uv is an extremely fast Python package and project manager, written in Rust."
tags:
  - "clippings"
---
[Skip to content](https://docs.astral.sh/uv/getting-started/help/#getting-help)

## Getting help

The `--help` flag can be used to view the help menu for a command, e.g., for `uv`:

```js
$ uv --help
```

To view the help menu for a specific command, e.g., for `uv init`:

```js
$ uv init --help
```

When using the `--help` flag, uv displays a condensed help menu. To view a longer help menu for a command, use `uv help`:

```js
$ uv help
```

To view the long help menu for a specific command, e.g., for `uv init`:

```js
$ uv help init
```

When using the long help menu, uv will attempt to use `less` or `more` to "page" the output so it is not all displayed at once. To exit the pager, press `q`.

## Displaying verbose output

The `-v` flag can be used to display verbose output for a command, e.g., for `uv sync`:

```js
$ uv sync -v
```

The `-v` flag can be repeated to increase verbosity, e.g.:

```js
$ uv sync -vv
```

Often, the verbose output will include additional information about why uv is behaving in a certain way.

## Viewing the version

When seeking help, it's important to determine the version of uv that you're using — sometimes the problem is already solved in a newer version.

To check the installed version:

```js
$ uv self version
```

The following are also valid:

```js
$ uv --version      # Same output as \`uv self version\`
$ uv -V             # Will not include the build commit and date
```

Note

Before uv 0.7.0, `uv version` was used instead of `uv self version`.

## Troubleshooting issues

The reference documentation contains a [troubleshooting guide](https://docs.astral.sh/uv/reference/troubleshooting/) for common issues.

## Open an issue on GitHub

The [issue tracker](https://github.com/astral-sh/uv/issues) on GitHub is a good place to report bugs and request features. Make sure to search for similar issues first, as it is common for someone else to encounter the same problem.

## Chat on Discord

Astral has a [Discord server](https://discord.com/invite/astral-sh), which is a great place to ask questions, learn more about uv, and engage with other community members.