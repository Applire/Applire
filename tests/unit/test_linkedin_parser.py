"""
Iteration 9 — LinkedIn parser unit tests (ZIP + PDF)

Tests applire/services/linkedin.py without any network or database calls.
ZIP fixtures are constructed inline using Python's stdlib zipfile + io.
PDF happy-path tests mock PdfReader so no real PDF generation is needed.

Run:
    pytest tests/unit/test_iter9_linkedin_parser.py -v
"""
import io
import zipfile
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# ZIP fixture helpers
# ---------------------------------------------------------------------------

_PROFILE_CSV = """\
First Name,Last Name,Headline,Location,Summary
Anna,Bauer,Senior Account Manager | Pharma & Life Sciences,Munich Germany,Experienced account manager in the DACH pharma sector.
"""

_POSITIONS_CSV = """\
Title,Company Name,Description,Location,Started On,Finished On
Senior Account Manager,Varena Deutschland GmbH,Managed key accounts in the oncology division.,Munich,Mar 2020,
Account Manager,Novartis AG,Responsible for DACH territory sales.,Munich,Jun 2017,Feb 2020
"""

_EDUCATION_CSV = """\
School Name,Degree Name,Field Of Study,Start Date,End Date,Notes,Activities
Ludwig-Maximilians-Universität München,Master of Science,Pharmazie,2012,2017,,
"""

_SKILLS_CSV = """\
Name,0
Veeva CRM,
Salesforce,
Key Account Management,
DACH Sales,
Oncology,
"""


def _make_zip(files: dict[str, str]) -> bytes:
    """Build a ZIP archive from a dict of filename → CSV content."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


_FULL_ZIP = _make_zip(
    {
        "Profile.csv": _PROFILE_CSV,
        "Positions.csv": _POSITIONS_CSV,
        "Education.csv": _EDUCATION_CSV,
        "Skills.csv": _SKILLS_CSV,
    }
)

_NO_SKILLS_ZIP = _make_zip(
    {
        "Profile.csv": _PROFILE_CSV,
        "Positions.csv": _POSITIONS_CSV,
    }
)

_EMPTY_ZIP = _make_zip({})


# ---------------------------------------------------------------------------
# parse_linkedin_zip — happy path (full export)
# ---------------------------------------------------------------------------


def test_full_export_returns_string():
    from applire.services.linkedin import parse_linkedin_zip

    result = parse_linkedin_zip(_FULL_ZIP)
    assert isinstance(result, str)
    assert len(result) > 100


def test_full_export_contains_name():
    from applire.services.linkedin import parse_linkedin_zip

    result = parse_linkedin_zip(_FULL_ZIP)
    assert "Anna" in result
    assert "Bauer" in result


def test_full_export_contains_headline():
    from applire.services.linkedin import parse_linkedin_zip

    result = parse_linkedin_zip(_FULL_ZIP)
    assert "Account Manager" in result


def test_full_export_contains_location():
    from applire.services.linkedin import parse_linkedin_zip

    result = parse_linkedin_zip(_FULL_ZIP)
    assert "Munich" in result


def test_full_export_contains_work_history():
    from applire.services.linkedin import parse_linkedin_zip

    result = parse_linkedin_zip(_FULL_ZIP)
    assert "Varena" in result
    assert "Novartis" in result


def test_full_export_contains_education():
    from applire.services.linkedin import parse_linkedin_zip

    result = parse_linkedin_zip(_FULL_ZIP)
    assert "München" in result or "Universitat" in result or "Pharmazie" in result


def test_full_export_contains_skills():
    from applire.services.linkedin import parse_linkedin_zip

    result = parse_linkedin_zip(_FULL_ZIP)
    assert "Veeva" in result or "Salesforce" in result


def test_full_export_section_headers_present():
    from applire.services.linkedin import parse_linkedin_zip

    result = parse_linkedin_zip(_FULL_ZIP)
    assert "PROFILE" in result
    assert "WORK EXPERIENCE" in result
    assert "SKILLS" in result


# ---------------------------------------------------------------------------
# parse_linkedin_zip — partial export (missing Skills.csv)
# ---------------------------------------------------------------------------


def test_partial_export_no_skills_does_not_raise():
    from applire.services.linkedin import parse_linkedin_zip

    result = parse_linkedin_zip(_NO_SKILLS_ZIP)
    assert isinstance(result, str)
    assert len(result) > 50


def test_partial_export_still_contains_profile_data():
    from applire.services.linkedin import parse_linkedin_zip

    result = parse_linkedin_zip(_NO_SKILLS_ZIP)
    assert "Anna" in result
    assert "Varena" in result


def test_partial_export_no_skills_section_header():
    """When Skills.csv is missing there should be no SKILLS section header."""
    from applire.services.linkedin import parse_linkedin_zip

    result = parse_linkedin_zip(_NO_SKILLS_ZIP)
    assert "=== SKILLS ===" not in result


# ---------------------------------------------------------------------------
# parse_linkedin_zip — case-insensitive file lookup
# ---------------------------------------------------------------------------


def test_lowercase_filenames_are_found():
    from applire.services.linkedin import parse_linkedin_zip

    zip_bytes = _make_zip(
        {
            "profile.csv": _PROFILE_CSV,
            "positions.csv": _POSITIONS_CSV,
            "skills.csv": _SKILLS_CSV,
        }
    )
    result = parse_linkedin_zip(zip_bytes)
    assert "Anna" in result
    assert "Varena" in result
    assert "Veeva" in result or "Salesforce" in result


def test_nested_path_filenames_are_found():
    """LinkedIn sometimes nests CSVs under a subdirectory in the ZIP."""
    from applire.services.linkedin import parse_linkedin_zip

    zip_bytes = _make_zip(
        {
            "Basic_LinkedInDataExport_01-01-2026/Profile.csv": _PROFILE_CSV,
            "Basic_LinkedInDataExport_01-01-2026/Positions.csv": _POSITIONS_CSV,
            "Basic_LinkedInDataExport_01-01-2026/Skills.csv": _SKILLS_CSV,
        }
    )
    result = parse_linkedin_zip(zip_bytes)
    assert "Anna" in result
    assert "Varena" in result


# ---------------------------------------------------------------------------
# parse_linkedin_zip — error cases
# ---------------------------------------------------------------------------


def test_empty_zip_raises_value_error():
    from applire.services.linkedin import parse_linkedin_zip

    with pytest.raises(ValueError, match="no recognisable data"):
        parse_linkedin_zip(_EMPTY_ZIP)


def test_invalid_bytes_raises_value_error():
    from applire.services.linkedin import parse_linkedin_zip

    with pytest.raises(ValueError, match="valid ZIP"):
        parse_linkedin_zip(b"this is not a zip file at all")


def test_pdf_bytes_raises_value_error():
    """A PDF file uploaded as a ZIP must raise ValueError."""
    from applire.services.linkedin import parse_linkedin_zip

    pdf_header = b"%PDF-1.4 fake pdf content"
    with pytest.raises(ValueError):
        parse_linkedin_zip(pdf_header)


# ---------------------------------------------------------------------------
# parse_linkedin_pdf — happy path (PdfReader mocked; no real PDF needed)
# ---------------------------------------------------------------------------

_SAMPLE_PROFILE_TEXT = (
    "Anna Bauer\nSenior Account Manager | Pharma & Life Sciences\n"
    "Munich, Germany\n\nExperience\nVarena Deutschland GmbH\n"
    "Senior Account Manager\nMar 2020 – Present\n\nSkills\nPython, Salesforce"
)


def _mock_reader(text: str) -> MagicMock:
    """Return a MagicMock that behaves like a one-page PdfReader."""
    page = MagicMock()
    page.extract_text.return_value = text
    reader = MagicMock()
    reader.pages = [page]
    return reader


def test_linkedin_pdf_returns_string():
    from applire.services.linkedin import parse_linkedin_pdf

    with patch("applire.services.linkedin.PdfReader", return_value=_mock_reader(_SAMPLE_PROFILE_TEXT)):
        result = parse_linkedin_pdf(b"fake-pdf-bytes")
    assert isinstance(result, str)
    assert len(result) > 0


def test_linkedin_pdf_contains_header_marker():
    from applire.services.linkedin import parse_linkedin_pdf

    with patch("applire.services.linkedin.PdfReader", return_value=_mock_reader(_SAMPLE_PROFILE_TEXT)):
        result = parse_linkedin_pdf(b"fake-pdf-bytes")
    assert "=== LINKEDIN PROFILE PDF ===" in result


def test_linkedin_pdf_contains_extracted_text():
    from applire.services.linkedin import parse_linkedin_pdf

    with patch("applire.services.linkedin.PdfReader", return_value=_mock_reader(_SAMPLE_PROFILE_TEXT)):
        result = parse_linkedin_pdf(b"fake-pdf-bytes")
    assert "Anna" in result
    assert "Bauer" in result
    assert "Varena" in result


def test_linkedin_pdf_multipage_text_is_joined():
    """Text from multiple pages must all appear in the result."""
    from applire.services.linkedin import parse_linkedin_pdf

    page1 = MagicMock()
    page1.extract_text.return_value = "Page one text"
    page2 = MagicMock()
    page2.extract_text.return_value = "Page two text"
    reader = MagicMock()
    reader.pages = [page1, page2]

    with patch("applire.services.linkedin.PdfReader", return_value=reader):
        result = parse_linkedin_pdf(b"fake-pdf-bytes")
    assert "Page one text" in result
    assert "Page two text" in result


# ---------------------------------------------------------------------------
# parse_linkedin_pdf — error cases
# ---------------------------------------------------------------------------


def test_linkedin_pdf_invalid_bytes_raises_value_error():
    from applire.services.linkedin import parse_linkedin_pdf

    with pytest.raises(ValueError, match="valid PDF"):
        parse_linkedin_pdf(b"this is not a pdf at all")


def test_linkedin_pdf_blank_pages_raises_value_error():
    """A PDF whose pages yield no text must raise ValueError."""
    from applire.services.linkedin import parse_linkedin_pdf

    with patch("applire.services.linkedin.PdfReader", return_value=_mock_reader("")):
        with pytest.raises(ValueError, match="text"):
            parse_linkedin_pdf(b"fake-pdf-bytes")


def test_linkedin_pdf_zip_bytes_raises_value_error():
    """A ZIP file uploaded as a PDF must raise ValueError (not a valid PDF)."""
    from applire.services.linkedin import parse_linkedin_pdf

    zip_bytes = _make_zip({"Profile.csv": _PROFILE_CSV})
    with pytest.raises(ValueError):
        parse_linkedin_pdf(zip_bytes)
