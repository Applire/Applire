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

"""ADR-061 clause 2 — the stance guard's testimony adjudication prompt.

Narrow, single-question, single-token: does THIS interview turn state that the
candidate has TOKEN? Deliberately the smallest possible LLM surface — the
deterministic accept path (``stance.py::_grounded``) already resolves the
overwhelming majority of ops, so this prompt is only reached for the uncertain
band (a morphological miss: qualifier prefix, parenthetical gloss, compound
tail — see #305).

The model does the semantic work; ``stance.py`` verifies the returned
``quote`` is a LITERAL substring of the turn before ever trusting "yes" — an
adjudication whose citation does not check out is rejected, same discipline
the Oracle already uses for its own evidence quoting. Not to be confused with
the unrelated ``testimony/1`` free-text intake door (#258,
``schemas/testimony.py`` / ``reconcile/testimony_bridge.py``) — this module
answers "did the candidate state X", that one is a submission channel.
"""
from __future__ import annotations

STANCE_ADJUDICATION_SYSTEM_PROMPT = """You are verifying one narrow claim against one interview turn's own text.

You will be given a TOKEN (a skill, technology, language, or certification
name) and a TURN (the candidate's own words: an interview gap, question and
answer, or a single agent-channel answer). Decide, using ONLY the turn's own
wording, whether the candidate is stating they have TOKEN.

Respond with a single JSON object and nothing else:

  {"answer": "yes" | "no" | "unclear", "quote": "<verbatim span of TURN, or empty string>"}

Rules:
- "yes" only when the turn's own wording affirms TOKEN — even under a
  different surface form: a qualifier scoped by an earlier clause ("PP" a
  sentence after "SAP-Rollout" affirms "SAP PP"), an abbreviation the turn
  uses bare where TOKEN spells it out ("OEE" affirms "OEE (Overall Equipment
  Effectiveness)"), or a compound the turn writes differently ("Sauberraumbereich"
  affirms "Sauberraum-Management"). Do not require the literal TOKEN string.
- "no" when the turn's own wording denies or disclaims TOKEN.
- "unclear" when the turn neither affirms nor denies TOKEN, the wording is
  ambiguous, or you are not confident.
- "quote" MUST be copied byte-for-byte from TURN — no paraphrase, no
  translation, no fixed typos, no added or removed punctuation. If you cannot
  copy an exact verbatim span that supports your answer, respond "unclear"
  with an empty "quote" rather than inventing one — a fabricated quote is
  worse than no answer.

Output ONLY the JSON object, no prose, no markdown fences."""


def build_stance_adjudication_prompt(token: str, kind: str, turn_text: str) -> str:
    """The user-turn prompt: TOKEN + its kind + the turn's own raw text.

    ``turn_text`` is UNNORMALISED (the caller's own grounding text, before
    ``ats_audit._norm``) so the model quotes against, and the citation check
    verifies against, the candidate's actual words.
    """
    return (
        f"TOKEN ({kind}): {token}\n\n"
        f"TURN:\n{turn_text}\n\n"
        "Does TURN state that the candidate has TOKEN? Answer now."
    )
