"""Shape checks for the screening result JSON (docs/intake/screening-contract.md)."""

from scan_agent.screening import DOC_TYPES, UNKNOWN

CODES = {"missing_heading", "missing_field", "field_out_of_position", "seal_logo_not_found",
         "font_inconsistent", "alignment_off", "unexpected_text", "altered_region"}
CHECK_INTS = ("expected_headings_found", "expected_headings_total", "expected_fields_found",
              "expected_fields_total")
CHECK_SCORES = ("layout_score", "seal_logo_score", "font_consistency", "alignment_score")


def _unit(value) -> bool:
    return isinstance(value, int | float) and 0.0 <= value <= 1.0


def validate(result: dict) -> None:
    assert result["schema"] == 1
    assert isinstance(result["agent_version"], str)
    doc = result["doc_type"]
    assert doc["predicted"] in (*DOC_TYPES, UNKNOWN)
    assert _unit(doc["confidence"])
    assert set(doc["scores"]) == set(DOC_TYPES)
    assert all(_unit(v) for v in doc["scores"].values())
    if doc["predicted"] == UNKNOWN:
        assert "checks" not in result and "similarity" not in result and "fields" not in result
    else:
        checks = result["checks"]
        for key in CHECK_INTS:
            assert isinstance(checks[key], int) and checks[key] >= 0
        assert checks["expected_headings_found"] <= checks["expected_headings_total"]
        assert checks["expected_fields_found"] <= checks["expected_fields_total"]
        for key in CHECK_SCORES:
            assert checks[key] is None or _unit(checks[key]), key
    for indicator in result["indicators"]:
        assert set(indicator) == {"code", "severity", "detail", "box"}
        assert indicator["code"] in CODES
        assert _unit(indicator["severity"])
        assert isinstance(indicator["detail"], str) and len(indicator["detail"]) <= 120
        assert len(indicator["box"]) == 4 and all(isinstance(v, int) for v in indicator["box"])
    if "similarity" in result:
        sim = result["similarity"]
        assert _unit(sim["score"]) and sim["reference_count"] > 0 and sim["method"] == "orb+layout"
    for name, field in result.get("fields", {}).items():
        assert isinstance(name, str) and isinstance(field["value"], str) and field["value"]
        assert _unit(field["confidence"])
    assert isinstance(result["timings_ms"]["screening"], int)
