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

# Prompt version: v5
# Used by: services/gap.py (cluster_gaps, _carry_forward_clusters)
#
# v5 (Hazelnut build 7 / WP-G, 2026-11-07, line 787:213): the input view stopped calling a
# demonstrated requirement "missing". The #260 keyword liabilities (required, claimable, no
# worked example yet) were listed under "Category C gaps (missing evidence)", and the model
# merged Priya's demonstrated "Adult intensive care" with the merely desirable "Paediatric
# intensive care" into one card whose jd_context read "The role expects intensive care
# experience across adult and paediatric settings" (UX audit round 3, rec 189, luna) — a
# misstatement of the posting, shown as an open topic to a nurse with four adult-ICU years.
# The liabilities now come as their own list, and jd_context states each requirement at the
# posting's own weight (required = expects, nice-to-have = a plus), never merged into one.
# Sonnet 5.5 replay (rec 189's lists, n=5 per arm): before, Adult ICU merged with the
# desirable paediatric item 2/5 with "expects ... with paediatric ... as additional assets";
# a first v5 draft dropped the story items from every cluster 5/5 (they are #260's way into
# the interview), so the every-gap rule now names the story list explicitly.
#
# v4 (Hazelnut build 4 / WP-G, 2026-10-24, line 787:95): on a recompute the call clusters only
# the concepts no carried topic holds, and it never saw those topics, so a concept the posting
# merely renamed ("Kunststoffverpackungen" for a carried "Verpackungsindustrie") came back as a
# brand-new card (Nougat K-1 delivery, 8 -> 9 clusters). The carried topics are now listed as
# EXISTING TOPICS and a gap may be filed under one by its id; services/gap.py folds such a
# cluster into the carried card. No block (a first analysis) = v3 behaviour.
#
# v3 (Hazelnut build 3 / WP-G, 2026-10-22): jd_context is a third-person sentence about the
# POSTING. v2 asked for a "first-person role perspective" and the model wrote it as the
# candidate speaking ("Ich stelle sichere Arbeitsbedingungen ... sicher", "I need German
# professional recognition") — 87 of 88 clusters over the 12-run baseline matrix. The gaps
# page shows it under "Was die Stelle erwartet:" and the interview prompt reads it as
# "JD context:", so a sentence in the candidate's voice put words in their mouth on both.
#
# v2 (#675 line 60, 2026-09-18): the cluster's B/C category is no longer solicited.
# It was stated here as a rule the model could only restate - "C if any constituent
# gap is Category C, else B" - and it is not a judgement at all: which input list a
# gap came from is a FACT the caller already holds (ADR-062 clause 1). Measured over
# every captured clustering record (63 records, 198 clusters, logs/llm + backend/
# logs/llm): 53 clusters carried a category that contradicted their own members,
# all 53 understating severity. cluster_gaps() now derives it.

import json

CLUSTERING_SYSTEM_PROMPT = """\
You are an expert career analyst. Your task is to group semantically related skill gaps into \
named clusters for a DACH job applicant.

Input: lists of Category B and Category C gaps from a gap analysis, an optional list of \
requirements the profile already shows but tells no worked example of yet (stories to tell, \
NOT missing - never describe them as gaps), plus the job's required and nice-to-have skills. \
Every entry of all three gap lists is clustered.

Output: a JSON object with a single key "clusters" whose value is an array of cluster objects. \
Each cluster absorbs multiple gaps that share the same semantic domain.

Schema for each cluster object:
{
  "id": "cluster-<label-kebab-lowercase>",
  "label": "Human-readable cluster name (concise, 2-5 words)",
  "gaps": ["exact gap strings absorbed into this cluster"],
  "jd_skills": ["matching entries from required_skills or nice_to_have_skills"],
  "jd_context": "One sentence about the POSTING, in the third person, written in the OUTPUT LANGUAGE stated in the user message: what the role expects in this area and why it matters for the role's work."
}

Rules:
- Merge gaps that share the same semantic domain (not just keywords)
- Copy each gap into "gaps" EXACTLY as it is written in the input list - same \
wording, same language, same spelling. Do not rephrase, expand or translate a gap, \
and never add one that is not in the two input lists.
- jd_skills: only include skills that directly motivate this cluster (may be empty)
- jd_context: one sentence, written entirely in the OUTPUT LANGUAGE stated in the user message \
(the candidate's UI language), regardless of the language of the gaps or the job description. \
It describes the POSTING - what the role expects in this area and why the work needs it - in the \
third person, e.g. "Die Stelle erwartet Erfahrung mit MES und Maschinendatenerfassung, weil die \
Fertigung digitalisiert wird." / "The role expects hands-on Kafka experience because payment \
events are processed as streams." It states each requirement at the POSTING'S OWN WEIGHT: what is in the required skills the role \
expects; what is only in the nice-to-have skills is a plus ("Paediatric intensive care experience \
is a plus"), never something the role expects. Never merge a required and a nice-to-have item \
into one expectation ("across adult and paediatric settings") - name each at its own weight, or \
keep them in separate clusters. It is shown to the candidate as what the role expects, so it \
never speaks as or to anyone: no first or second person (no ich/wir/du/Sie, no I/we/you), and it \
never says whether the candidate has or lacks anything.
- EXISTING TOPICS: when the user message lists existing topics, they are clusters the \
candidate already sees. A gap that belongs to one of them (the same subject, even in other \
words) goes into a cluster with that topic's EXACT "id" and "label" copied from the list; only \
gaps that fit no existing topic get a new cluster with a new id. Never copy an existing \
topic's members into "gaps" - only the input gaps.
- Target 5-12 clusters; never more clusters than input gaps
- Every input gap must appear in exactly one cluster - the gaps of ALL THREE lists, the \
"no worked example yet" list included. Such an item is a REQUIRED skill the candidate will be \
asked to tell a story about: cluster it like any other gap (alone or with related items of the \
same weight), and its jd_context says what the role expects of it - never that it is missing
- Respond ONLY with a valid JSON object of the form {"clusters": [ ... ]} - no markdown, no explanations"""


def build_clustering_prompt(
    category_b: list[str],
    category_c: list[str],
    required_skills: list[str],
    nice_to_have_skills: list[str],
    lang: str = "de",
    existing_topics: list[dict] | None = None,
    stories: list[str] | None = None,
) -> str:
    # #3 (ADR-038): the gaps page is conversational — jd_context follows the candidate's
    # UI language, not the JD's. An explicit directive is more reliable than "the JD's language".
    language_name = "GERMAN" if lang == "de" else "ENGLISH"
    # ADR-084 embedding point 22 (Form A). Everything above the OUTPUT LANGUAGE
    # line is the posting's derivatives, and this call's output — the cluster
    # labels and `jd_context` — is what the candidate is SHOWN on the gaps page
    # and asked about in the interview (points 25a-c).
    from applire.services.untrusted_text import fence

    return (
        fence(
            "Category C gaps (missing evidence):\n"
            + json.dumps(category_c, ensure_ascii=False)
            + "\n\nCategory B gaps (likely but unstated):\n"
            + json.dumps(category_b, ensure_ascii=False)
            + (
                "\n\nShown in the profile, no worked example yet (a story to tell, not missing):\n"
                + json.dumps(stories, ensure_ascii=False)
                if stories
                else ""
            )
            + "\n\nRequired skills from JD:\n"
            + json.dumps(required_skills, ensure_ascii=False)
            + "\n\nNice-to-have skills from JD:\n"
            + json.dumps(nice_to_have_skills, ensure_ascii=False)
            + (
                "\n\nEXISTING TOPICS (file a gap under one of these ids when it belongs there):\n"
                + json.dumps(existing_topics, ensure_ascii=False)
                if existing_topics
                else ""
            ),
            header="GAPS AND JD SKILLS TO CLUSTER",
        )
        + "\n\n"
        f"OUTPUT LANGUAGE: write every cluster's jd_context entirely in {language_name}.\n\n"
        'Group the gaps into semantic clusters. Return a JSON object: {"clusters": [ ... ]}.'
    )
