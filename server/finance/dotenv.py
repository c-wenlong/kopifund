#!/usr/bin/env python3
"""A small `.env` reader, stdlib only.

The Notion integration token lives in `.env` at the repo root, which git
ignores. This module reads that file and nothing else: `KEY=value` lines, an
optional `export ` prefix, one layer of single or double quotes stripped, `#`
comments and blank lines skipped.

Deliberately not supported, so that what the file says is what you get:
variable interpolation (`$OTHER`), escape sequences inside quotes, multi-line
values, and writing anything back into `os.environ`. A real environment
variable always wins over the file, which is what `lookup` is for: nothing here
can quietly replace a value the shell already set.
"""
import os

__all__ = ["parse", "read", "lookup"]


def parse(text):
    """`KEY=value` text -> {key: value}, later lines winning over earlier ones.

    A line is skipped when it is blank, when it starts (after leading spaces)
    with `#`, when it has no `=`, or when its key is empty. In an unquoted
    value a `#` that follows whitespace starts a comment; inside quotes, and
    with no space in front of it, a `#` is part of the value.
    """
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export ") or line.startswith("export\t"):
            line = line[len("export"):].lstrip()
        key, sep, val = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        if not key:
            continue
        out[key] = _value(val.strip())
    return out


def _value(val):
    """One right-hand side: quotes stripped, or an inline comment cut off."""
    if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
        return val[1:-1]
    for marker in (" #", "\t#"):
        cut = val.find(marker)
        if cut != -1:
            val = val[:cut]
    return val.strip()


def read(path):
    """Parse the file at `path`. A missing or unreadable file reads as {}.

    Unreadable covers the cases a token file really hits: no permission, a
    directory where a file was expected, bytes that are not UTF-8. None of
    them should stop the run before the later lookups get their turn.
    """
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError):
        return {}
    return parse(text)


def lookup(name, path, environ=None):
    """`name` from the real environment, else from the `.env` file at `path`.

    Returns None when neither has it, and never overrides the environment: an
    exported variable wins even when the file names the same key.
    """
    environ = os.environ if environ is None else environ
    val = environ.get(name)
    if val:
        return val
    return read(path).get(name) or None
