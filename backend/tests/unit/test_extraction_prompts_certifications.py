# Copyright (C) 2026 Tobias Rosenbaum
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify it under the
# terms of the GNU Affero General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option) any
# later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU Affero General Public License for more
# details.
#
# You should have received a copy of the GNU Affero General Public License along
# with this program. If not, see <https://www.gnu.org/licenses/>.
"""#190 regression guard — BOTH extraction prompts must declare certifications.

There are two CV/profile extraction prompts, reached by different channels:

  * ``cv_extraction`` — the browser ``/upload`` and import-job path.
  * ``profile_extraction`` — the ``import_from_text`` / ``import_from_pdf`` path,
    which is what the MCP/agent ``import_cv`` tool, LinkedIn export import, and the
    paste-text import all call.

The original #190 fix only added the ``certifications`` section + precedence rule
to ``cv_extraction``. So certifications kept getting dropped on the agent/MCP +
LinkedIn channels — the exact channel #190 was reported on (a LinkedIn export on
edge UAT). The mock provider masked this because ``_PROFILE_PARSE_RESPONSE`` already
returns certifications regardless of what the *prompt* asks for, so an
import-through-mock test passes either way. These prompt-content assertions guard
the real defect: every extraction prompt must ASK the model for certifications and
tell it a certifications heading outranks the skills routing.
"""

from applire.prompts import cv_extraction, profile_extraction

_PRECEDENCE_MARKER = "CERTIFICATIONS TAKE PRECEDENCE"


def test_profile_extraction_prompt_requests_certifications():
    # The import_from_text / MCP / LinkedIn path — the channel #190 was reported on.
    assert '"certifications"' in profile_extraction.SYSTEM_PROMPT
    assert _PRECEDENCE_MARKER in profile_extraction.SYSTEM_PROMPT


def test_cv_extraction_prompts_request_certifications():
    # The browser /upload path — guard the original fix from regressing.
    assert "certifications" in cv_extraction.GENERIC_CV_EXTRACTION_PROMPT
    assert _PRECEDENCE_MARKER in cv_extraction.GENERIC_CV_EXTRACTION_PROMPT
    assert _PRECEDENCE_MARKER in cv_extraction.JD_AWARE_CV_EXTRACTION_PROMPT
