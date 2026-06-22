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

"""
US144 — labelled grounding/hallucination regression corpus.

Each case is a known fabrication the ADR-021 judges must catch. Used by:
  - tests/unit/test_grounding_corpus.py        (deterministic, no LLM — corpus + prompt contract)
  - tests/integration/test_grounding_corpus_llm.py (real LLM — asserts the judge rejects)

Case keys:
  id              short stable identifier
  failure_class   the FMEA failure mode (JF-M-*)
  source          the truthful source material (raw CV text for extraction; profile dict for tailoring)
  source_anchor   a verbatim substring of the source that MUST be carried into the reviewer prompt
  draft           the extracted/tailored JSON containing the fabrication
  fabricated_token a verbatim substring of the fabrication that is ABSENT from the source
  why             human note
"""

# ── Extraction-side fabrications (JF-M-3.1) ───────────────────────────────────
# build_cv_extraction_review_prompt(raw_cv_text: str, extracted_json: dict)

_EXTRACTION_SOURCE = (
    "Siemens AG — Software Engineer (Jan 2020 – Dec 2022)\n"
    "- Built internal tooling in Python\n"
    "- Supported the migration of legacy services\n"
)

EXTRACTION_CASES = [
    {
        "id": "ext-invented-cert",
        "failure_class": "JF-M-3.1",
        "source": _EXTRACTION_SOURCE,
        "source_anchor": "Siemens AG",
        "draft": {
            "work_experience": [
                {"company": "Siemens AG", "role": "Software Engineer",
                 "start_date": "2020-01", "end_date": "2022-12",
                 "responsibilities": ["Built internal tooling in Python"], "achievements": [], "technologies": ["Python"]}
            ],
            "certifications": [{"name": "AWS Certified Solutions Architect"}],
        },
        "fabricated_token": "AWS Certified Solutions Architect",
        "why": "No certification appears anywhere in the source CV text.",
    },
    {
        "id": "ext-invented-date",
        "failure_class": "JF-M-3.1",
        "source": "Globex — Analyst\n- Reporting and dashboards\n",  # no dates in source
        "source_anchor": "Analyst",
        "draft": {
            "work_experience": [
                {"company": "Globex", "role": "Analyst",
                 "start_date": "2015-03", "end_date": "2019-08",
                 "responsibilities": ["Reporting and dashboards"], "achievements": [], "technologies": []}
            ],
        },
        "fabricated_token": "2015-03",
        "why": "Source states no dates; start_date must be null, not invented.",
    },
    {
        "id": "ext-garbled-employer",
        "failure_class": "JF-M-3.1",
        "source": _EXTRACTION_SOURCE,
        "source_anchor": "Siemens AG",
        "draft": {
            "work_experience": [
                {"company": "Siemes AG", "role": "Software Engineer",
                 "start_date": "2020-01", "end_date": "2022-12",
                 "responsibilities": ["Built internal tooling in Python"], "achievements": [], "technologies": ["Python"]}
            ],
        },
        "fabricated_token": "Siemes AG",
        "why": "Employer name is garbled (mis-transcribed) from 'Siemens AG'.",
    },
    {
        "id": "ext-fabricated-entry",
        "failure_class": "JF-M-3.1",
        "source": _EXTRACTION_SOURCE,
        "source_anchor": "Siemens AG",
        "draft": {
            "work_experience": [
                {"company": "Siemens AG", "role": "Software Engineer",
                 "start_date": "2020-01", "end_date": "2022-12",
                 "responsibilities": ["Built internal tooling in Python"], "achievements": [], "technologies": ["Python"]},
                {"company": "Initech LLC", "role": "Senior Developer",
                 "start_date": "2017-01", "end_date": "2019-12",
                 "responsibilities": ["Led a team of 8"], "achievements": [], "technologies": []},
            ],
        },
        "fabricated_token": "Initech LLC",
        "why": "Second employer has no basis in the source text.",
    },
]


# ── Legitimate extractions that MUST be approved (US171 false-positive guard) ──
# These drafts faithfully paraphrase / split / merge the source and invent nothing.
# The pre-US171 verbatim reviewer over-flagged exactly this shape, exhausting retries.
# `paraphrase_token` is a draft string that is NOT a verbatim copy of the source (so it
# genuinely exercises paraphrase tolerance) yet is fully supported by the source's meaning.

_LEGIT_SOURCE = (
    "Müller GmbH — Backend Developer (2019 – 2022)\n"
    "- Responsible for designing and maintaining the company's REST API platform.\n"
    "- Mentored two junior developers and ran the on-call rotation.\n"
)

LEGITIMATE_EXTRACTION_CASES = [
    {
        "id": "legit-paraphrase-split",
        "source": _LEGIT_SOURCE,
        "source_anchor": "REST API platform",
        # paraphrase ("Responsible for designing and maintaining" → "Designed and maintained"),
        # plus a sentence split ("Mentored …" / "ran the on-call rotation" → two bullets) and a
        # reword ("ran" → "Managed"). Nothing here is invented.
        "draft": {
            "work_experience": [
                {
                    "company": "Müller GmbH",
                    "role": "Backend Developer",
                    "start_date": "2019",
                    "end_date": "2022",
                    "responsibilities": [
                        "Designed and maintained the REST API platform",
                        "Mentored two junior developers",
                        "Managed the on-call rotation",
                    ],
                    "achievements": [],
                    "technologies": ["REST"],
                }
            ],
        },
        "paraphrase_token": "Managed the on-call rotation",
        "why": "Paraphrase + sentence split + reword, all source-supported — must NOT be flagged.",
    },
]


# ── Cross-role misattribution that MUST be rejected (US171 priority check A) ────
# The moved content IS present in the source (so it is not a pure fabrication) but is
# attached to the WRONG employer/role on a multi-role CV.

_MISATTRIB_SOURCE = (
    "Acme GmbH — Software Developer (2015 – 2018)\n"
    "- Built internal reporting tools in Python.\n"
    "\n"
    "Globex AG — Engineering Manager (2018 – 2022)\n"
    "- Led a team of 8 engineers and cut deployment time by 40%.\n"
)

MISATTRIBUTION_EXTRACTION_CASES = [
    {
        "id": "ext-misattributed-leadership",
        "failure_class": "JF-M-3.1",
        "source": _MISATTRIB_SOURCE,
        "source_anchor": "Globex AG",
        "draft": {
            "work_experience": [
                {
                    "company": "Acme GmbH",
                    "role": "Software Developer",
                    "start_date": "2015",
                    "end_date": "2018",
                    # leadership belongs to the Globex Engineering Manager role, not here
                    "responsibilities": [
                        "Built internal reporting tools in Python",
                        "Led a team of 8 engineers",
                    ],
                    "achievements": [],
                    "technologies": ["Python"],
                },
                {
                    "company": "Globex AG",
                    "role": "Engineering Manager",
                    "start_date": "2018",
                    "end_date": "2022",
                    "responsibilities": ["Cut deployment time by 40%"],
                    "achievements": [],
                    "technologies": [],
                },
            ],
        },
        "misattributed_content": "Led a team of 8 engineers",
        "correct_employer": "Globex AG",
        "wrong_employer": "Acme GmbH",
        "why": "Leadership of 8 engineers is real (under Globex) but misattributed to the Acme "
               "Software Developer role — the achievement landed under the wrong employer.",
    },
]


# ── Projects-block cases (US172 — ADR-044) ─────────────────────────────────────
# Standalone personal project with NO employer — must be APPROVED (the key false-positive
# guard: absence of employer must NOT trigger shell/fabricated/empty flagging).

_PROJECT_LEGIT_SOURCE = (
    "Open-source project: csv-utils (2021 – 2023)\n"
    "- Maintained a Python library for CSV parsing and transformation.\n"
    "- Used by 200+ developers on GitHub.\n"
)

LEGITIMATE_PROJECT_CASES = [
    {
        "id": "proj-legit-standalone-no-employer",
        "source": _PROJECT_LEGIT_SOURCE,
        "source_anchor": "csv-utils",
        # paraphrase: "Used by 200+ developers" → "Adopted by over 200 developers"
        "paraphrase_token": "Adopted by over 200 developers",
        "draft": {
            "work_experience": [],
            "projects": [
                {
                    "name": "csv-utils",
                    "employer": None,
                    "start_date": "2021",
                    "end_date": "2023",
                    "description": "Maintained a Python library for CSV parsing and transformation.",
                    "achievements": ["Adopted by over 200 developers on GitHub"],
                    "technologies": ["Python", "CSV"],
                }
            ],
        },
        "why": "Standalone personal project with no employer — paraphrase OK, nothing invented. "
               "Must NOT be flagged as a shell/empty/fabricated entry due to missing employer.",
    },
]

# Project with an invented date — must be REJECTED.

_PROJECT_REJECT_SOURCE = (
    "Side project: DataViz Dashboard\n"
    "- Built an interactive dashboard for visualising sales data.\n"
    "- No dates provided.\n"
)

REJECT_PROJECT_CASES = [
    {
        "id": "proj-invented-date",
        "failure_class": "JF-M-3.1",
        "source": _PROJECT_REJECT_SOURCE,
        "source_anchor": "DataViz Dashboard",
        "draft": {
            "work_experience": [],
            "projects": [
                {
                    "name": "DataViz Dashboard",
                    "employer": None,
                    "start_date": "2020-06",
                    "end_date": "2021-03",
                    "description": "Built an interactive dashboard for visualising sales data.",
                    "achievements": [],
                    "technologies": [],
                }
            ],
        },
        "fabricated_token": "2020-06",
        "why": "Source states no dates for this project; start_date must be null, not invented.",
    },
]


# ── Tailoring-side fabrications (JF-M-6.1 / 6.2) ───────────────────────────────
# build_review_prompt(source_material: str, tailored_json: dict)  (source serialised to JSON)

_TAILORING_SOURCE = {
    "work_history": [
        {"company": "Acme GmbH", "role": "Software Developer",
         "start_date": "2020-01", "end_date": "2022-12",
         "bullets": ["Built REST APIs", "Contributed to a database migration"]}
    ],
    "skills": ["Python", "PostgreSQL"],
    "certifications": [],
}

TAILORING_CASES = [
    {
        "id": "tail-fabricated-bullet",
        "failure_class": "JF-M-6.1",
        "source": _TAILORING_SOURCE,
        "source_anchor": "Acme GmbH",
        "draft": {
            "contact": {"name": "Max Muster"},
            "summary": "Backend engineer.",
            "work_history": [
                {"company": "Acme GmbH", "role": "Software Developer",
                 "start_date": "2020-01", "end_date": "2022-12",
                 "bullets": ["Built REST APIs", "Architected a Kubernetes platform serving 10M users"]}
            ],
            "skills": ["Python", "PostgreSQL"],
        },
        "fabricated_token": "Kubernetes platform serving 10M users",
        "why": "Kubernetes/scale claim has no basis in the profile bullets.",
    },
    {
        "id": "tail-fabricated-cert",
        "failure_class": "JF-M-6.1",
        "source": _TAILORING_SOURCE,
        "source_anchor": "Acme GmbH",
        "draft": {
            "contact": {"name": "Max Muster"},
            "summary": "PMP certified delivery lead.",
            "work_history": [
                {"company": "Acme GmbH", "role": "Software Developer",
                 "start_date": "2020-01", "end_date": "2022-12",
                 "bullets": ["Built REST APIs"]}
            ],
            "skills": ["Python", "PostgreSQL"],
        },
        "fabricated_token": "PMP certified",
        "why": "No PMP (or any) certification exists in the source profile.",
    },
    {
        "id": "tail-oversell-title",
        "failure_class": "JF-M-6.2",
        "source": _TAILORING_SOURCE,
        "source_anchor": "Software Developer",
        "draft": {
            "contact": {"name": "Max Muster"},
            "summary": "Lead Architect.",
            "work_history": [
                {"company": "Acme GmbH", "role": "Lead Architect",
                 "start_date": "2020-01", "end_date": "2022-12",
                 "bullets": ["Led a company-wide database migration"]}
            ],
            "skills": ["Python", "PostgreSQL"],
        },
        "fabricated_token": "Lead Architect",
        "why": "Title inflated from 'Software Developer'; 'Led' overstates 'Contributed to'.",
    },
    {
        "id": "tail-oversell-verb-magnitude",
        "failure_class": "JF-M-6.2",
        "source": _TAILORING_SOURCE,
        "source_anchor": "Contributed to",
        "draft": {
            "contact": {"name": "Max Muster"},
            "summary": "Backend engineer.",
            "work_history": [
                {"company": "Acme GmbH", "role": "Software Developer",
                 "start_date": "2020-01", "end_date": "2022-12",
                 "bullets": ["Built REST APIs", "Led a team of 12 on a database migration"]}
            ],
            "skills": ["Python", "PostgreSQL"],
        },
        "fabricated_token": "Led a team of 12",
        "why": "Verb + magnitude oversell (US169): source says 'Contributed to a database "
               "migration' — no leadership, no team size. 'Led a team of 12' inflates both.",
    },
    {
        "id": "tail-mutated-date",
        "failure_class": "JF-M-6.1",
        "source": _TAILORING_SOURCE,
        "source_anchor": "Acme GmbH",
        "draft": {
            "contact": {"name": "Max Muster"},
            "summary": "Backend engineer.",
            "work_history": [
                {"company": "Acme GmbH", "role": "Software Developer",
                 "start_date": "2017-01", "end_date": "2022-12",
                 "bullets": ["Built REST APIs"]}
            ],
            "skills": ["Python", "PostgreSQL"],
        },
        "fabricated_token": "2017-01",
        "why": "start_date mutated from 2020-01 to 2017-01 — extends tenure by 3 years.",
    },
]
