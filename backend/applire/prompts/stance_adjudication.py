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
name) and a TURN (an interview turn — Applire's QUESTION and the candidate's
ANSWER — or a single agent-channel answer). Decide, using ONLY the turn's own
wording, whether the candidate is stating they have TOKEN.

Respond with a single JSON object and nothing else:

  {"answer": "yes" | "no" | "unclear", "quote": "<verbatim span of TURN, or empty string>"}

When TURN is split into a QUESTION and an ANSWER, only the ANSWER is the
candidate's own words. The QUESTION is Applire's and states nothing about the
candidate: it only tells you what the ANSWER replies to.

Rules:
- "yes" only when the turn's own wording affirms TOKEN — even under a
  different surface form: a qualifier scoped by an earlier clause ("PP" a
  sentence after "SAP-Rollout" affirms "SAP PP"), an abbreviation the turn
  uses bare where TOKEN spells it out ("OEE" affirms "OEE (Overall Equipment
  Effectiveness)"), or a compound the turn writes differently ("Sauberraumbereich"
  affirms "Sauberraum-Management"). Do not require the literal TOKEN string.
  A TOKEN that appears only in the QUESTION is affirmed only when the ANSWER
  itself says yes to it ("Ja, das habe ich verantwortet") — quote that.
- TAKING PART IS NOT HAVING IT: an ANSWER that the candidate was involved in,
  took part in or contributed to something ("war beteiligt", "habe
  mitgewirkt", "was involved in") does not affirm TOKEN for that topic unless
  it states that the candidate did TOKEN themself — "unclear".
- "no" when the turn's own wording denies or disclaims TOKEN.
- "unclear" when the turn neither affirms nor denies TOKEN, the wording is
  ambiguous, or you are not confident.
- "quote" MUST be copied byte-for-byte from the ANSWER (from TURN when it is
  not split) — no paraphrase, no
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


#: The two headings of a built-in interview TURN (ADR-061 amended 2026-10-18,
#: DEL-5 (1)). The adjudicator reads them; ``stance._citation_verified`` checks
#: the returned quote against the ANSWER alone.
TURN_QUESTION_HEADING = "QUESTION (Applire's — context only, not the candidate's words):"
TURN_ANSWER_HEADING = "ANSWER (the candidate's own words):"


def render_interview_turn(*, gap: str | None, question: str | None, answer: str) -> str:
    """A built-in interview turn split into Applire's QUESTION and the
    candidate's ANSWER (ADR-061 amended 2026-10-18, DEL-5 (1)).

    Before, the turn was the three fields joined into one undifferentiated
    string, so the adjudicator — and the deterministic accept ahead of it — had
    no way to tell the question's term ("Investitionsplanung") from the
    candidate's testimony. With no question and no gap the answer is returned
    as-is (the agent-channel shape).
    """
    context = " — ".join(p.strip() for p in (gap, question) if p and p.strip())
    if not context:
        return answer
    return f"{TURN_QUESTION_HEADING}\n{context}\n\n{TURN_ANSWER_HEADING}\n{answer}"

