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


def test_a_story_figure_stated_as_prozent_is_reported_missing_though_present():
    """The reviewer-facing consequence: a curated story figure of 80% is
    genuinely on the delivered page, phrased as '80 Prozent' (idiomatic German,
    not a paraphrase failure) — and `figures_missing_from` still raises it as a
    BLOCKING demand, because figure IDENTITY (kind:value) does not match across
    the '%'-sign / spelled-unit boundary the docstring says it does."""
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

    # And the raw figures_present() set used underneath tells the same story:
    # "percent:80" (the key figures_missing_from looks for) is simply absent.
    present_keys = figures_present(document_text)
    assert "percent:80" not in present_keys
    assert "number:80" in present_keys
