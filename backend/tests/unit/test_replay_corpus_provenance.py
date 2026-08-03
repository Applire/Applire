# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-073 — the PII control over committed replay fixtures.

The captured corpus is not uniformly safe to commit from. It spans
2026-07-06 → 2026-08-02; founder-acceptance runs 1–6 used a real posting and a
real profile, while charter runs 10–16 used the synthetic cases under
`tests/files/panel_review_case/` ("Synthetic personas — no real personal data").

`backend/tests/fixtures/replay/README.md` states the rule. **This file is what
makes it a control rather than a convention**: a fixture carrying an identity
that is not on the synthetic allowlist turns the build red.

Deliberately an allowlist, not a denylist of real names. A denylist would need
the real names written down in a public repo — the opposite of the goal — and
would silently pass any name nobody thought to add.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "replay"

# Personas and employers from tests/files/panel_review_case/ — all invented.
SYNTHETIC_IDENTITIES = frozenset(
    {
        "Stefan Brandt",
        "Katrin Hoffmann",
        "Daniel",
        "Priya",
        "Emma",
        "Marcus",
        "Anna Bauer",
        "Weberit Kunststofftechnik",
        "Rheinwerk Verpackungen",
        "Schwarzwald Präzision",
        "Rasselstein Umformtechnik",
        "Arnold Antriebstechnik",
        "Söhne Maschinenbau",
    }
)

# Two capitalised words in a row: the shape a person's name takes. Deliberately
# over-broad — it flags "Lean Management" too, which is why the assertion below
# is scoped to names that look like PEOPLE (a known first name) rather than to
# every match. Over-broad here costs a maintainer one allowlist entry; too narrow
# costs a real name in a public repo.
_NAME_RE = re.compile(r"\b([A-ZÄÖÜ][a-zäöüß]{2,})\s+([A-ZÄÖÜ][a-zäöüß]{2,})\b")

_SYNTHETIC_FIRST_NAMES = frozenset(
    ident.split()[0] for ident in SYNTHETIC_IDENTITIES if " " in ident
) | {"Daniel", "Priya", "Emma", "Marcus", "Anna"}


def _fixture_files() -> list[Path]:
    return sorted(
        p for p in FIXTURE_DIR.iterdir() if p.suffix in {".json", ".jsonl"}
    )


def test_there_is_at_least_one_fixture_to_check():
    """Guard against the guard going inert. An empty directory would make every
    assertion below vacuously true — the shape of a control that cannot fire."""
    assert _fixture_files(), (
        f"no replay fixtures found in {FIXTURE_DIR} — this control has nothing "
        "to check, which is indistinguishable from it passing"
    )


@pytest.mark.parametrize("path", _fixture_files(), ids=lambda p: p.name)
def test_no_fixture_carries_a_non_synthetic_person(path: Path):
    """Every person-shaped name in a committed fixture must be on the allowlist."""
    text = path.read_text(encoding="utf-8")
    offenders = {
        f"{first} {last}"
        for first, last in _NAME_RE.findall(text)
        if first in _SYNTHETIC_FIRST_NAMES and f"{first} {last}" not in SYNTHETIC_IDENTITIES
    }
    assert not offenders, (
        f"{path.name} carries person-shaped names that are not on the synthetic "
        f"allowlist: {sorted(offenders)}. Either the fixture was cut from a "
        "founder-acceptance run (real profile data — do not commit it), or a new "
        "synthetic persona needs adding to SYNTHETIC_IDENTITIES."
    )


@pytest.mark.parametrize("path", _fixture_files(), ids=lambda p: p.name)
def test_every_fixture_is_traceable_to_a_capture(path: Path):
    """A fixture with no provenance cannot be audited later, and cannot be judged
    stale when a prompt changes. Both shapes carry it: JSON files a `provenance`
    block, JSONL records the recorder's own `ts`/`stage` fields."""
    if path.suffix == ".json":
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert "provenance" in doc, f"{path.name} has no provenance block"
        for field in ("stage", "captured", "case_family"):
            assert field in doc["provenance"], f"{path.name} provenance lacks {field!r}"
    else:
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert lines, f"{path.name} is empty"
        for i, line in enumerate(lines, 1):
            rec = json.loads(line)
            assert rec.get("ts"), f"{path.name}:{i} has no capture timestamp"
            assert rec.get("stage"), f"{path.name}:{i} has no stage label"


def test_no_fixture_carries_a_real_contact_string():
    """The synthetic cases use example.com throughout. Anything else is a leak."""
    bad: dict[str, set[str]] = {}
    for path in _fixture_files():
        text = path.read_text(encoding="utf-8")
        emails = {
            e for e in re.findall(r"[\w.+-]+@[\w.-]+\.\w+", text)
            if not e.endswith(("example.com", "example.org", "applire.de"))
        }
        if emails:
            bad[path.name] = emails
    assert not bad, f"non-example contact strings in replay fixtures: {bad}"
