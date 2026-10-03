# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""``python -m applire.admin`` — the operator's server-side account tools.

Run inside the backend container, e.g.::

    docker compose exec backend python -m applire.admin create-admin --email you@example.org

Subcommands are modules listed in ``SUBCOMMAND_MODULES``; each exposes
``register(subparsers) -> None`` and sets ``func`` (a coroutine function taking
the parsed ``argparse.Namespace`` and returning an exit code) on its parser.
A listed module that is not installed is skipped with a debug log, so the
tool works on a branch that has not received every subcommand yet
(Strawberry W1 seam: 1b adds ``applire.admin.reset``).
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import logging
import sys

SUBCOMMAND_MODULES: tuple[str, ...] = ("applire.admin.create_admin", "applire.admin.reset")

logger = logging.getLogger("applire.admin")


def build_parser(modules: tuple[str, ...] = SUBCOMMAND_MODULES) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m applire.admin",
        description="Applire account tools for the person who runs the server.",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.required = True
    for name in modules:
        try:
            module = importlib.import_module(name)
        except ImportError as exc:
            if exc.name != name:  # the module exists but one of ITS imports failed
                raise
            logger.debug("admin subcommand module %s not installed — skipped", name)
            continue
        module.register(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(asyncio.run(args.func(args)) or 0)


if __name__ == "__main__":
    sys.exit(main())
