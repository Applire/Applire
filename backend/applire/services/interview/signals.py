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

# Deterministic done-signal detection — no LLM call, runs before ResponseParser.
#
# Design note (#216): agents drive the interview in natural language, so the
# old exact-set lookup ("done" only) left the session un-endable — "I'm done."
# or "I'm done with the interview, please wrap up" were treated as answers.
# We now normalise (fold apostrophes/punctuation/unicode) and match against
# single-token signals, whole-message phrases, AND a bounded "I'm done…" opener
# for leading framing. A false POSITIVE ends the interview prematurely, so the
# matcher stays conservative: multi-word forms must be the WHOLE message (a
# signal word buried in a real answer never fires), and a negation guard blocks
# "I'm not done". Anything not matched here falls through to the LLM parser.

import re
import unicodedata

# Single-token end signals (matched against the fully normalised message).
TERMINATION_SIGNALS: frozenset[str] = frozenset(
    {
        # English
        "done",
        "skip",
        "finish",
        "end",
        "stop",
        # German
        "fertig",
        "überspringen",
        "abschließen",
        "ende",
        "stopp",
    }
)

# Whole-message termination phrases (normalised: lowercased, apostrophes and
# punctuation removed, whitespace collapsed). Matched against the ENTIRE
# message, so "that's all the Python I know, but …" is NOT a signal.
TERMINATION_PHRASES: frozenset[str] = frozenset(
    {
        # English
        "that is all",
        "thats all",
        "im done",
        "i am done",
        "im all done",
        "i am all done",
        "im finished",
        "i am finished",
        "were done",
        "we are done",
        "no more questions",
        "nothing more to add",
        "nothing else",
        "that is everything",
        "thats everything",
        "please wrap up",
        "lets wrap up",
        "wrap up",
        "that covers it",
        # German
        "das wars",
        "das war es",
        "das wars dann",
        "das war es dann",
        "ich bin fertig",
        "wir sind fertig",
        "das reicht",
        "das genügt",
        "keine weiteren fragen",
        "nichts weiter",
    }
)

# Negation guards — if any appear, the message is never a termination signal
# ("I'm not done", "noch nicht fertig").
_NEGATIONS: tuple[str, ...] = (
    "not done",
    "not finished",
    "not yet",
    "nicht fertig",
    "noch nicht",
)

# Leading-framing opener for the strongly-terminal English "done" family:
# "I'm done with the interview, please wrap up" → terminate. "finished" is
# deliberately excluded here (it collides with "I'm finished migrating X, but…")
# and is covered only as a whole-message phrase above.
_DONE_OPENER = re.compile(r"^(im|i am|were|we are)\s+(all\s+)?done\b")


def _normalize(message: str) -> str:
    """Fold to a punctuation-free, apostrophe-free, lowercased token stream.

    NFKC first (so unicode look-alikes collapse), then strip the apostrophe
    variants explicitly (U+2019 does not fold to ASCII under NFKC — the same
    trap that once defeated an ASCII marker list), then punctuation→space.
    """
    s = unicodedata.normalize("NFKC", message).lower().strip()
    s = s.replace("'", "").replace("’", "").replace("`", "")
    s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE)
    return re.sub(r"\s+", " ", s).strip()


def is_termination_signal(message: str) -> bool:
    """Return True if the message is a recognised session-end signal.

    Normalised, punctuation-tolerant, and phrase-aware — but conservative:
    a signal word inside a substantive answer never fires, and negations are
    excluded. No LLM call; runs before the LLM ResponseParser as a fast path.
    """
    norm = _normalize(message)
    if not norm:
        return False
    if any(neg in norm for neg in _NEGATIONS):
        return False
    if norm in TERMINATION_SIGNALS:
        return True
    if norm in TERMINATION_PHRASES:
        return True
    return bool(_DONE_OPENER.match(norm))
