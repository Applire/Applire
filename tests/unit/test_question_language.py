# Copyright (C) 2024-2026 Tobias Rosenbaum
#
# This file is part of Applire. AGPL-3.0-or-later — see LICENSE.

from applire.prompts.interview import with_language, QUESTION_SYSTEM_PROMPT


def test_with_language_de_names_german():
    out = with_language(QUESTION_SYSTEM_PROMPT, "de")
    assert "German" in out
    assert "OUTPUT LANGUAGE" in out
    assert out.startswith(QUESTION_SYSTEM_PROMPT)  # original preserved


def test_with_language_en_names_english():
    out = with_language(QUESTION_SYSTEM_PROMPT, "en")
    assert "English" in out
    assert "never mirror" in out.lower()


def test_with_language_unknown_falls_back_to_english():
    out = with_language(QUESTION_SYSTEM_PROMPT, "fr")
    assert "English" in out
