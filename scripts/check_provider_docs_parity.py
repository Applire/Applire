#!/usr/bin/env python3
# Copyright (C) 2024-2026 Tobias Rosenbaum
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Applire is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Applire. If not, see <https://www.gnu.org/licenses/>.
"""
Provider documentation parity check.

Ground truth is the factory in backend/applire/providers/llm/__init__.py: the set
of values _build_provider() accepts. Every public surface that tells a self-hoster
which providers exist must name exactly that set.

Born from a real self-hoster report (2026-08-30): LLM_PROVIDER=anthropic was
documented in .env.example while the image they ran rejected it, and the English
README's prerequisites listed four of six providers while the German one listed
all six. Both are drift this check makes loud.

Usage:  python3 scripts/check_provider_docs_parity.py
Exit:   0 = every surface agrees, 1 = drift (details on stdout)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# `mock` and `replay` are test-only providers; they are deliberately undocumented.
INTERNAL_ONLY = {"mock", "replay"}


def truth() -> set[str]:
    """The providers _build_provider() actually accepts."""
    src = (ROOT / "backend/applire/providers/llm/__init__.py").read_text(encoding="utf-8")
    body = src.split("def _build_provider", 1)[-1]
    return set(re.findall(r'provider == "([a-z0-9_]+)"', body)) - INTERNAL_ONLY


def from_choose_line(path: Path, marker: str) -> tuple[set[str], str] | None:
    """The `# LLM Provider — choose one: a | b | c` line."""
    for line in path.read_text(encoding="utf-8").splitlines():
        if marker in line:
            names = re.split(r"\s*\|\s*", line.split(marker, 1)[1].strip())
            return {n.strip() for n in names if n.strip()}, line.strip()
    return None


def from_options_table(path: Path) -> tuple[set[str], str]:
    """Every `LLM_PROVIDER=x` in the provider options table."""
    text = path.read_text(encoding="utf-8")
    return set(re.findall(r"`LLM_PROVIDER=([a-z0-9_]+)`", text)), "provider options table"


def from_prerequisites(path: Path, marker: str) -> tuple[set[str], str] | None:
    """The Prerequisites bullet — prose, so match each provider name case-insensitively.

    Asymmetric on purpose: candidates come from truth(), so this surface detects a
    provider the prose FORGOT but not one it invents. The choose-one line and the
    options table cover the invented-provider direction for every file.
    """
    for line in path.read_text(encoding="utf-8").splitlines():
        if marker in line:
            low = line.lower()
            return {p for p in truth() if p in low}, line.strip()[:90] + "…"
    return None


def main() -> int:
    expected = truth()
    if not expected:
        print("FAIL  could not parse any provider from _build_provider()")
        return 1

    surfaces: list[tuple[str, tuple[set[str], str] | None]] = [
        (".env.example → choose-one line",
         from_choose_line(ROOT / ".env.example", "choose one:")),
        ("README.md → choose-one line",
         from_choose_line(ROOT / "README.md", "choose one:")),
        ("README.md → options table",
         from_options_table(ROOT / "README.md")),
        ("README.md → prerequisites",
         from_prerequisites(ROOT / "README.md", "LLM provider of your choice")),
        ("README.de.md → choose-one line",
         from_choose_line(ROOT / "README.de.md", "wähle einen:")),
        ("README.de.md → options table",
         from_options_table(ROOT / "README.de.md")),
        ("README.de.md → prerequisites",
         from_prerequisites(ROOT / "README.de.md", "LLM-Anbieter deiner Wahl")),
    ]

    print(f"Providers accepted by _build_provider(): {', '.join(sorted(expected))}\n")

    failed = False
    for label, found in surfaces:
        if found is None:
            print(f"FAIL  {label}\n      anchor not found — the surface moved or was renamed")
            failed = True
            continue
        names, evidence = found
        if names == expected:
            print(f"ok    {label}")
            continue
        failed = True
        missing, extra = sorted(expected - names), sorted(names - expected)
        print(f"FAIL  {label}")
        if missing:
            print(f"      documented nowhere here: {', '.join(missing)}")
        if extra:
            print(f"      documented but NOT accepted by the factory: {', '.join(extra)}")
        print(f"      source: {evidence}")

    if failed:
        print("\nProvider documentation has drifted from the factory.")
        print("A provider named in .env.example but absent from the running image is")
        print("exactly the HTTP 500 a self-hoster hits with no way to diagnose it.")
        return 1

    print("\nAll provider surfaces agree with the factory.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
