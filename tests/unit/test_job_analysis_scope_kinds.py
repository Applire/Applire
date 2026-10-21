# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""UX audit M6 (ruling UX-1, Hazelnut build 2) — a scope bar is the ROLE's own number.

The writer's v10 rule said "team_size counts PEOPLE", which admitted the employer's
headcount, a product's users and a count of teams; the reviewer's check 2c said the
same, so it approved them. Measured on the PQ fixture postings (openai/gpt-5.6-luna,
thinking off, extraction + review loop, n=5 per posting): wrong-kind scope entries
in 3 of 20 analyses before (JD02 "zwei Entwicklungsteams" → team_size 2; JD05 the
offered salary → budget; JD05 one person split off as its own bar), 0 of 30 after,
with the true bars kept (JD07 11 MA 5/5, JD04 5/5, JD01 5/5, JD05 9/10). These pins
are not that evidence — they keep the two prompts saying the same thing.
"""


def test_the_writer_names_whose_number_a_scope_bar_is_and_the_look_alikes():
    from applire.prompts.job_analysis import SYSTEM_PROMPT

    assert "both are bars on the ROLE ITSELF" in SYSTEM_PROMPT
    for look_alike in ("employer's headcount", "users, patients, customers",
                       "count of teams", "never the salary or benefits"):
        assert look_alike in SYSTEM_PROMPT, look_alike
    # The examples are deliberately NOT the PQ fixture sentences the replay used.
    for fixture_text in ("220 Mitarbeitende", "800.000", "zwei agilen", "90.000–105.000"):
        assert fixture_text not in SYSTEM_PROMPT, fixture_text


def test_the_reviewer_check_2c_uses_the_same_definition():
    from applire.prompts.review_job_analysis import JOB_ANALYSIS_REVIEW_SYSTEM_PROMPT as R

    assert "team_size counts the people THIS ROLE leads" in R
    assert "money THIS ROLE" in R
    for look_alike in ("employer's", "headcount", "users or customers", "count of teams",
                       "offered salary"):
        assert look_alike in R, look_alike
    assert "team_size counts PEOPLE and budget is MONEY" not in R  # the admitting wording
