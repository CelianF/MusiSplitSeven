"""Shared entry point for ``python -m musisplit`` and the packaged exe."""
from __future__ import annotations


def run(argv: list[str]) -> int:
    if len(argv) > 1 and argv[1] == "--self-test":
        from .selftest import main as selftest

        return selftest(argv[2:])
    from .gui import main

    return main()
