# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Display casing for a person's name (Hazelnut build 3, 784 polish (b)).

A CV header is often typeset in capitals ("STEFAN BRANDT"), and extraction
transcribes it that way into ``personal_info.name``. Printed as a letter's
sign-off, the capitals read like a form field, not a signature ("DANIEL KOVAČ").
:func:`display_name` restores display casing for a name that is ENTIRELY in
capitals. Casing only: no character is added, removed or reordered. A name that
already carries lower-case letters is the candidate's own spelling and is
returned unchanged ("McAllister", "de la Cruz" stay as written).
"""

from __future__ import annotations

import re

# Name particles written lower-case inside a DACH/European name ("von der Heide",
# "van Dijk"). Never applied to the first word, which is always capitalised.
_PARTICLES = frozenset({
    "von", "vom", "van", "der", "den", "de", "du", "da", "di", "del", "della",
    "dos", "das", "zu", "zum", "zur", "ter", "ten", "la", "le", "y",
})

_SEPARATORS = re.compile(r"([\s\-'’]+)")


def _cap(part: str) -> str:
    return part[:1].upper() + part[1:].lower()


def display_name(name: str) -> str:
    """``"STEFAN BRANDT"`` → ``"Stefan Brandt"``; ``"ANNA-LENA VON DER HEIDE"`` →
    ``"Anna-Lena von der Heide"``; ``"DANIEL KOVAČ"`` → ``"Daniel Kovač"``.
    Returns the input unchanged unless every letter in it is upper-case and it
    has at least two letters."""
    if not isinstance(name, str):
        return name
    letters = [c for c in name if c.isalpha()]
    if len(letters) < 2 or any(not c.isupper() for c in letters):
        return name
    out: list[str] = []
    first_word = True
    for token in _SEPARATORS.split(name):
        if not token or _SEPARATORS.fullmatch(token):
            out.append(token)
            continue
        low = token.lower()
        if not first_word and low in _PARTICLES and (not out or out[-1].strip() == ""):
            out.append(low)
        else:
            out.append(_cap(token))
        first_word = False
    return "".join(out)
