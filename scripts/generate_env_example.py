#!/usr/bin/env python3
# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
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

"""Regenerate `.env.example` from the settings registry (ADR-087 clause 3).

    python3 scripts/generate_env_example.py            # rewrite the file
    python3 scripts/generate_env_example.py --check    # fail if it would change

`.env.example` is a published release asset (`releases/latest/download/env.example`
— the README's install path, checked by `.github/workflows/compose-guard.yml`),
so a hand-maintained copy is exactly how the shipped template drifts from the
code. `--check` runs in the unit suite; a forgotten regeneration is a red test,
not a self-hoster's surprise.

Deliberately dependency-free beyond the backend package: it must run in CI
without a database, so importing it may not construct `Settings`.
"""

from __future__ import annotations

import argparse
import os
import sys
import textwrap
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))

# `applire.config` builds a Settings() at import time and requires DATABASE_URL.
# The registry itself needs no settings object, but importing the package must
# not explode in a bare CI shell.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from applire.settings_registry import (  # noqa: E402
    SECTION_ORDER,
    SettingEntry,
    all_settings,
)

TARGET = _REPO_ROOT / ".env.example"

HEADER = """\
# Applire — Environment Configuration
# Copy to .env and fill in your values:  cp .env.example .env
#
# GENERATED FILE — do not edit by hand.
# Every variable below is declared once in backend/applire/settings_registry.py
# (ADR-087); this file is rendered from it by scripts/generate_env_example.py and
# a unit test fails when the two drift. To change a comment or a default, edit the
# registry entry and re-run the generator.
#
# Lines that are commented out are OPTIONAL: the value shown is what the code uses
# when you leave them alone. Deleting an uncommented line falls back to the same
# default — the examples here ARE the code defaults, except where a line says
# otherwise.
"""

_SECTION_BLURB = {
    "Database": "Database",
    "LLM provider": "LLM provider — bring your own key",
    "LLM behaviour": "LLM behaviour — timeouts, caps, diagnostics",
    "Operations": "Operations — logging and topology",
    "Retention (GDPR)": "Retention — GDPR TTLs enforced by the retention worker (ADR-005)",
    "Storage and uploads": "Storage and uploads",
    "Network and access": "Network and access",
    "Advanced tuning": (
        "Advanced tuning — read by constants.py and config.py.\n"
        "Declared so nothing is invisible, not because you should change it.\n"
        "Leave these alone unless a real run gives you a reason."
    ),
}


#: The first public release. Anything introduced in it is "always been there",
#: so the generated file does not repeat it 50 times.
BASELINE_RELEASE = "0.31.0"

#: Comment width, "# " included. Long prose wraps; a description that already
#: carries its own line breaks keeps them (those were laid out by hand).
_WIDTH = 88


def _comment_block(text: str) -> list[str]:
    out: list[str] = []
    for line in text.strip().splitlines():
        stripped = line.rstrip()
        if not stripped:
            out.append("#")
            continue
        for wrapped in textwrap.wrap(
            stripped,
            width=_WIDTH - 2,
            break_long_words=False,
            break_on_hyphens=False,
        ) or [""]:
            out.append(f"# {wrapped}")
    return out


def render_entry(entry: SettingEntry) -> list[str]:
    """The comment block plus the assignment line for one entry."""
    lines = _comment_block(entry.description)
    # Show the code default only when the example genuinely hides a usable one.
    # For an empty-by-default credential the placeholder is self-explanatory and
    # "code default: (empty)" is noise.
    if (
        entry.example is not None
        and entry.example != entry.default
        and not (entry.secret and not entry.default)
    ):
        shown_default = entry.default or "(empty)"
        lines.append(f"# code default: {shown_default}")
    # Provenance, so an operator comparing their .env against a newer template can
    # see at a glance what is new to them and what has changed under them. BASELINE
    # is the first public release, and printing "since 0.31.0" on two thirds of the
    # file would be noise, so only later arrivals say so.
    if entry.introduced_in != BASELINE_RELEASE:
        lines.append(f"# since: {entry.introduced_in}")
    if entry.semantics_changed_in:
        lines.append(f"# meaning changed in: {entry.semantics_changed_in}")
    prefix = "#" if entry.commented else ""
    lines.append(f"{prefix}{entry.env_var}={entry.shown_value}")
    return lines


def render() -> str:
    lines: list[str] = HEADER.rstrip("\n").splitlines()
    for section in SECTION_ORDER:
        entries = [
            e for e in all_settings() if e.section == section and e.in_env_example
        ]
        if not entries:
            continue
        lines.append("")
        lines.append("# " + "=" * 74)
        for blurb_line in _SECTION_BLURB[section].splitlines():
            lines.append(f"# {blurb_line}")
        lines.append("# " + "=" * 74)
        for entry in entries:
            lines.append("")
            lines.extend(render_entry(entry))
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if .env.example differs from the generated output",
    )
    args = parser.parse_args(argv)

    rendered = render()
    current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else None

    if args.check:
        if current == rendered:
            print(f"{TARGET.name} is up to date ({len(all_settings())} settings).")
            return 0
        print(
            f"{TARGET.name} is out of date with backend/applire/settings_registry.py.\n"
            "Run:  python3 scripts/generate_env_example.py",
            file=sys.stderr,
        )
        return 1

    TARGET.write_text(rendered, encoding="utf-8")
    print(f"Wrote {TARGET} ({len(all_settings())} settings).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
