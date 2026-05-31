from applire.services.cv_gap_mapper import map_gaps_to_sections


def test_gap_maps_to_section_with_most_keyword_overlap():
    sections = {
        "introduction": "experienced python developer with django",
        "position::abc": "built rest apis using python flask",
        "skills": "java sql git",
    }
    gaps = ["python", "django"]
    result = map_gaps_to_sections(gaps, sections)
    # Each gap is assigned to a single section (highest token overlap; ties break
    # to the first section). "python" ties introduction/position::abc (1 token
    # each) → introduction wins; "django" only matches introduction.
    assert result["introduction"] == ["python", "django"]
    assert "position::abc" not in result
    assert "skills" not in result or result["skills"] == []


def test_unmatched_gap_goes_to_general():
    sections = {"introduction": "java developer", "skills": "java"}
    gaps = ["kubernetes"]
    result = map_gaps_to_sections(gaps, sections)
    assert result.get("__general__") == ["kubernetes"]


def test_empty_gaps_returns_empty():
    sections = {"introduction": "some text"}
    result = map_gaps_to_sections([], sections)
    assert result == {}
