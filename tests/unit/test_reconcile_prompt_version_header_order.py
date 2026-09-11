# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The reconcile prompt's version header exists so a future reader can trace
"what changed, in what order, with what measured effect" without re-deriving
it from git log (`prompts/reconcile.py`'s own header: "Version history — every
rule change with its MEASURED effect"). WP-P2 (adversarial pass, 2026-09-11)
found the header's entries physically OUT of chronological order after three
separate P2 commits each inserted their own entry in the wrong place: the
2026-09-11 sub-sequence read (top-to-bottom) M5.1.1(3) [19,007] -> NOT SHIPPED
M5.1.1(2) -> M5.1.4 [18,819] -> M5.1.1(1) [18,703] -> M-3a [18,562, actually
2026-09-09] -- inverted relative to the arms A0(M-3a)->A1(M5.1.4)->
A2(M5.1.1(1))->A3(M5.1.1(3))->A4(NOT SHIPPED, reverted) the P2 report's own
measurement table (`Documents/Runs/Nougat/build-2/p2/report.md` Sec.
"Measurements") establishes. Fixed by reordering; this test pins the order so
a future entry-insertion cannot re-scramble it.
"""

import re

from applire.prompts import reconcile as reconcile_module

_ENTRY_RE = re.compile(r"^\* \*\*(.+?)\*\*", re.MULTILINE)

# The label each entry's own bold lead-in is expected to contain, in the
# EXACT physical order they must appear (chronological, oldest first --
# the convention `prompts/reconcile.py`'s own pre-existing entries already
# established before P2 touched the file).
_EXPECTED_LABEL_ORDER = [
    "15,118 chars, 14 rules",
    "16,244 (#684",
    "16,660 (M-1,",
    "17,599 (M-2,",
    "18,562 (M-3a,",
    "18,819 (M5.1.4,",
    "18,703 (M5.1.1 (1),",
    "19,007 (M5.1.1 (3),",
    "NOT SHIPPED",
]


def test_version_header_entries_are_chronological():
    """Every `* **...**` entry in the module docstring, in physical order,
    must match `_EXPECTED_LABEL_ORDER` -- the construction order the arms
    A0 (M-3a) -> A1 (M5.1.4) -> A2 (M5.1.1 (1)) -> A3 (M5.1.1 (3)) ->
    A4 (NOT SHIPPED, reverted) actually ran in."""
    doc = reconcile_module.__doc__ or ""
    found = _ENTRY_RE.findall(doc)
    assert found, "no version-header entries found in prompts/reconcile.py's docstring"
    matched = [
        next(label for label in _EXPECTED_LABEL_ORDER if entry.startswith(label))
        for entry in found
    ]
    assert matched == _EXPECTED_LABEL_ORDER, (
        f"version header out of order: {matched} != {_EXPECTED_LABEL_ORDER}"
    )


def test_the_final_shipped_entry_matches_the_actual_prompt_length():
    """The header's own self-report for the LAST (most recent, non-reverted)
    entry must equal `len(RECONCILE_SYSTEM_PROMPT)` -- a mismatch means the
    measured arm and the shipped text have drifted apart (evidence no longer
    describes the code it was measured against)."""
    prompt_len = len(reconcile_module.RECONCILE_SYSTEM_PROMPT)
    assert prompt_len == 19007, (
        f"RECONCILE_SYSTEM_PROMPT is {prompt_len} chars; the version header's "
        "last shipped entry claims 19,007 -- update whichever one drifted"
    )
