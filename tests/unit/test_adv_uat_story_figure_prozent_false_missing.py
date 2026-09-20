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

"""Adversarial finding (Nougat UAT-fixes batch, adv pass, 2026-09-20) on WP-D's
F-5 SIGNATURE STORY FIGURES block (`services/story_reach.py`, reviewer check 11,
ruling D-3).

`figures_missing_from`'s own docstring states, as its whole justification for
matching by figure IDENTITY rather than by literal substring:

    "Presence is figure identity, not string matching: ``80 %`` and ``80%`` and
    ``80 Prozent`` are the same figure to ``extract_figures``, and a demand
    keyed on the literal would be raised against a document that already
    carries the number."

Measured against the real `extract_figures` (`oracle.matchers.figures.py`,
US244, the shared canonical figure detector — ADR-066): the first two claims
hold (`80%` and `80 %` both extract as `Figure(kind="percent", value="80")`),
but the third does not. `_PERCENT_RE` requires a literal `%` character
(`r"[~≈]?\\s*(\\d+(?:[.,]\\d+)?)\\s*%"`); "80 Prozent" contains no `%`, so it
falls through to `_NUMBER_RE` instead and extracts as
`Figure(kind="number", value="80")` — a DIFFERENT kind. Since
`StoryFigure.key` / `_figure_key` is `f"{kind}:{value}"`, a story figure
recorded as kind "percent" (`percent:80`) is never satisfied by a document
that states the same fact as "80 Prozent" (`number:80`): the keys differ.

Consequence: the reviewer's per-round SIGNATURE STORY FIGURES block would
report a story's figure as MISSING — a BLOCKING check-11 demand — for a
document that already states the exact same measured outcome, spelled the way
a German-language cover letter or CV would naturally spell it. This directly
falsifies the module's own documented invariant and reproduces the class of
harm ruling D-3 was built to prevent in the other direction (a genuine
STILL-MISSING figure): here the check spends a blocking round, and the
STORY_DEMAND_LIMIT budget, demanding something that is already on the page.

Deterministic, zero provider calls.

**The fix** (adversarial pass, same-branch, CLOSED): `story_reach.py` now
normalises spelled-out percent forms ("80 Prozent", "80 percent", "80 per
cent") to the symbol form ("80 %") before `extract_figures` runs — on BOTH the
story's own `outcome` text (:func:`story_figures`) and the composed document
text (:func:`figures_missing_from`) — via the module-local
`_normalize_percent_words`. `oracle.matchers.figures.extract_figures` itself
is UNTOUCHED (nobody's file; the Oracle keeps its own contract): test 1 below
still pins that the raw, shared extractor does not unify these forms on its
own. Test 2 is now the GUARD: it pins the FIXED, module-level behaviour.
"""
from applire.services.load_bearing import figures_present
from applire.services.oracle.matchers.figures import extract_figures
from applire.services.story_reach import StoryFigure, figures_missing_from


def test_the_docstrings_own_claim_about_80_prozent_is_false():
    """Ground truth, asserted with the real predicate: `80%` and `80 %`
    extract to the same (kind, value) pair; `80 Prozent` does not."""
    percent_sign = extract_figures("um 80% reduziert")
    percent_sign_spaced = extract_figures("um 80 % reduziert")
    prozent_word = extract_figures("um 80 Prozent reduziert")

    assert [(f.kind, f.value) for f in percent_sign] == [("percent", "80")]
    assert [(f.kind, f.value) for f in percent_sign_spaced] == [("percent", "80")]
    assert [(f.kind, f.value) for f in prozent_word] != [("percent", "80")], (
        "the module docstring claims '80 Prozent' extracts as the same figure "
        "as '80%' — measured, it extracts as kind='number' instead, so the "
        "claim does not hold"
    )


def test_a_story_figure_stated_as_prozent_is_now_recognised_as_present():
    """GUARD (was the adversarial reproduction; flipped after the same-branch
    fix). The reviewer-facing consequence: a curated story figure of 80% is
    genuinely on the delivered page, phrased as '80 Prozent' (idiomatic German,
    not a paraphrase failure) — `figures_missing_from` must NOT raise it as a
    BLOCKING demand.

    Mutation: comment out the `_normalize_percent_words(...)` call in
    `figures_missing_from` (`story_reach.py`) on a scratchpad copy — this test
    goes red by name.
    """
    figure = StoryFigure(
        story_id="story-1",
        title="Shared validation strategy",
        raw="80%",
        kind="percent",
        value="80",
        entry_label="Novarex Biologics SE — AI Automation Lead",
    )
    document = {
        "body": {
            "paragraphs": [
                "Sehr geehrte Damen und Herren,",
                "Durch eine gemeinsame Validierungsstrategie an drei Standorten "
                "konnten wir den Validierungsaufwand um 80 Prozent senken.",
            ]
        }
    }
    document_text = " ".join(document["body"]["paragraphs"])

    # The fact is unambiguously on the page: a human reading it sees "80" tied
    # to the exact same reduction the story claims.
    assert "80" in document_text
    assert "Prozent" in document_text

    missing = figures_missing_from([figure], document)
    assert missing == [], (
        "the story's figure (80%) is stated in the delivered document as "
        "'80 Prozent' and should be recognised as PRESENT — instead "
        f"figures_missing_from reports it MISSING: {missing!r}. A reviewer "
        "wired to this block would raise check 11 as BLOCKING against a "
        "document that already carries the fact, spending the "
        "STORY_DEMAND_LIMIT budget on a false demand."
    )

    # The RAW, shared figures_present()/extract_figures() still do not unify
    # the spelled-out form on their own (the Oracle's own contract, untouched)
    # — "percent:80" is absent from the UN-normalised text.
    present_keys = figures_present(document_text)
    assert "percent:80" not in present_keys
    assert "number:80" in present_keys

    # It is `figures_missing_from`'s OWN pre-normalisation that closes the gap
    # for this module's callers — confirmed directly against the normalised text.
    from applire.services.story_reach import _normalize_percent_words

    normalized_keys = figures_present(_normalize_percent_words(document_text))
    assert "percent:80" in normalized_keys
