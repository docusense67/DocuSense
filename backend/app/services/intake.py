"""Incoming-document screening decision (S-04): pure functions, no database, no I/O.

Contract: docs/intake/screening-contract.md. The station measures and extracts; this module
alone decides whether a document passed screening (Tray 1) or needs manual review (Tray 2).
"Passed screening" only means no rule fired; it never says a document is authentic.

Parsing is tolerant where the contract asks for it: unknown keys are ignored and the optional
blocks (`similarity`, `fields`, `checks.seal_logo_score`) mean "not measured" when missing.
Required values that are missing or of the wrong type make the result malformed
(InvalidScreeningResult -> HTTP 422): a document is never passed on data that isn't there.
"""

import json
import math
from dataclasses import dataclass
from typing import Any, Protocol

from app.models.enums import IntakeDecision, IntakeDocType

SCHEMA_VERSION = 1
MAX_RESULT_CHARS = 256_000

TRAY_PASSED = 1
TRAY_MANUAL_REVIEW = 2

# Reason codes, one per rule, in the order the rules are checked.
QUALITY_NOT_OK = "quality_not_ok"
DOC_TYPE_UNKNOWN = "doc_type_unknown"
DOC_TYPE_LOW_CONFIDENCE = "doc_type_low_confidence"
SEVERE_INDICATOR = "severe_indicator"
LAYOUT_SCORE_LOW = "layout_score_low"
SIMILARITY_LOW = "similarity_low"
HEADINGS_MISSING = "expected_headings_missing"
# Added after routing, never by decide(): the gate disagreed with the assigned tray, or jammed.
ROUTED_TRAY_MISMATCH = "routed_tray_mismatch"
ROUTING_JAM = "routing_jam"

REASON_TEXT = {
    QUALITY_NOT_OK: "The photo of the page was not usable (blurred, too dark or bright, glare, "
                    "cut off or skewed), even after recapturing.",
    DOC_TYPE_UNKNOWN: "The station could not tell which document this is.",
    DOC_TYPE_LOW_CONFIDENCE: "The station was not confident about the document type.",
    SEVERE_INDICATOR: "The station flagged at least one feature to look at (see indicators).",
    LAYOUT_SCORE_LOW: "Headings and fields are not where this document type usually has them.",
    SIMILARITY_LOW: "The page looks unlike the reference samples of this document type.",
    HEADINGS_MISSING: "Some of the headings this document type should have were not found.",
    ROUTED_TRAY_MISMATCH: "The sorting gate put the page in a different tray than assigned.",
    ROUTING_JAM: "The page jammed while being sorted into a tray.",
}


class InvalidScreeningResult(ValueError):
    """The station's screening JSON is malformed (HTTP 422)."""


class ThresholdSource(Protocol):
    min_doc_type_confidence: float
    max_indicator_severity: float
    min_layout_score: float
    min_similarity_score: float


@dataclass(frozen=True)
class Thresholds:
    """Contract defaults. The admin-editable IntakeSettings row has the same attributes."""

    min_doc_type_confidence: float = 0.75  # confidence below this -> review
    max_indicator_severity: float = 0.5  # any indicator at or above this -> review
    min_layout_score: float = 0.70  # layout below this -> review
    min_similarity_score: float = 0.60  # similarity below this -> review (when measured)


DEFAULTS = Thresholds()


@dataclass(frozen=True)
class Screening:
    """The values of a screening result that the decision reads."""

    quality_ok: bool
    predicted: str  # as sent
    doc_type: IntakeDocType  # UNKNOWN for any name the backend does not know
    confidence: float
    severities: tuple[float, ...]
    indicators: tuple[dict, ...]
    layout_score: float | None  # None = not measured (only allowed for unknown documents)
    headings_found: int | None
    headings_total: int | None
    similarity: float | None  # None = not measured


def _reject_constant(name: str):
    raise InvalidScreeningResult(f"result: {name} is not a valid number")


def load_result(text: str) -> dict:
    """Parse the `result` form field (JSON object). NaN/Infinity are refused."""
    if len(text) > MAX_RESULT_CHARS:
        raise InvalidScreeningResult("result is too large")
    try:
        value = json.loads(text, parse_constant=_reject_constant)
    except (json.JSONDecodeError, RecursionError):
        raise InvalidScreeningResult("result must be JSON") from None
    if not isinstance(value, dict):
        raise InvalidScreeningResult("result must be a JSON object")
    return value


def _block(data: dict, key: str, *, optional: bool = False) -> dict | None:
    value = data.get(key)
    if value is None and optional:
        return None
    if not isinstance(value, dict):
        raise InvalidScreeningResult(f"result.{key} must be an object")
    return value


def _number(value: Any, where: str, *, unit: bool = True) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise InvalidScreeningResult(f"{where} must be a number")
    if unit and not 0 <= value <= 1:
        raise InvalidScreeningResult(f"{where} must be between 0 and 1")
    return float(value)


def _count(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InvalidScreeningResult(f"{where} must be a whole number >= 0")
    return value


def parse(result: Any) -> Screening:
    """Validate the parts of a screening result the decision needs."""
    if not isinstance(result, dict):
        raise InvalidScreeningResult("result must be a JSON object")
    schema = result.get("schema")
    if isinstance(schema, bool) or schema != SCHEMA_VERSION:
        raise InvalidScreeningResult(f"result.schema must be {SCHEMA_VERSION}")

    quality = _block(result, "quality")
    if not isinstance(quality.get("ok"), bool):
        raise InvalidScreeningResult("result.quality.ok must be true or false")

    doc_type = _block(result, "doc_type")
    predicted = doc_type.get("predicted")
    if not isinstance(predicted, str) or not predicted.strip():
        raise InvalidScreeningResult("result.doc_type.predicted must be a document type name")
    predicted = predicted.strip()
    known = {t.value for t in IntakeDocType}
    confidence = _number(doc_type.get("confidence"), "result.doc_type.confidence")

    # The station leaves `checks` out when it could not tell the document type: there is no
    # profile to check against. Such a page always goes to manual review (doc_type_unknown).
    is_unknown = predicted not in known or predicted == IntakeDocType.UNKNOWN.value
    checks = _block(result, "checks", optional=is_unknown)
    layout = found = total = None
    if checks is not None:
        layout = _number(checks.get("layout_score"), "result.checks.layout_score")
        found = _count(checks.get("expected_headings_found"),
                       "result.checks.expected_headings_found")
        total = _count(checks.get("expected_headings_total"),
                       "result.checks.expected_headings_total")

    raw_indicators = result.get("indicators")
    if not isinstance(raw_indicators, list):
        raise InvalidScreeningResult("result.indicators must be a list (empty when none)")
    severities = []
    for i, indicator in enumerate(raw_indicators):
        if not isinstance(indicator, dict):
            raise InvalidScreeningResult(f"result.indicators[{i}] must be an object")
        severities.append(_number(indicator.get("severity"), f"result.indicators[{i}].severity"))

    similarity_block = _block(result, "similarity", optional=True)
    similarity = None
    if similarity_block is not None and similarity_block.get("score") is not None:
        similarity = _number(similarity_block["score"], "result.similarity.score")

    return Screening(
        quality_ok=quality["ok"], predicted=predicted,
        doc_type=IntakeDocType(predicted) if predicted in known else IntakeDocType.UNKNOWN,
        confidence=confidence, severities=tuple(severities), indicators=tuple(raw_indicators),
        layout_score=layout, headings_found=found, headings_total=total, similarity=similarity,
    )


def reasons_for(screening: Screening, thresholds: ThresholdSource = DEFAULTS) -> list[str]:
    """Every rule that fires, in rule order. Empty = passed screening."""
    reasons = []
    if not screening.quality_ok:
        reasons.append(QUALITY_NOT_OK)
    if screening.doc_type == IntakeDocType.UNKNOWN:
        reasons.append(DOC_TYPE_UNKNOWN)
    if screening.confidence < thresholds.min_doc_type_confidence:
        reasons.append(DOC_TYPE_LOW_CONFIDENCE)
    if any(s >= thresholds.max_indicator_severity for s in screening.severities):
        reasons.append(SEVERE_INDICATOR)
    if screening.layout_score is not None and screening.layout_score < thresholds.min_layout_score:
        reasons.append(LAYOUT_SCORE_LOW)
    if screening.similarity is not None and screening.similarity < thresholds.min_similarity_score:
        reasons.append(SIMILARITY_LOW)
    if (screening.headings_found is not None and screening.headings_total is not None
            and screening.headings_found < screening.headings_total):
        reasons.append(HEADINGS_MISSING)
    return reasons


def tray_for(decision: IntakeDecision) -> int:
    return TRAY_PASSED if decision == IntakeDecision.PASSED_SCREENING else TRAY_MANUAL_REVIEW


def decide(result: Any, settings: ThresholdSource | None = None
           ) -> tuple[IntakeDecision, int, list[str]]:
    """(decision, tray, reasons) for one screening result. Raises InvalidScreeningResult."""
    reasons = reasons_for(parse(result), settings or DEFAULTS)
    decision = IntakeDecision.MANUAL_REVIEW if reasons else IntakeDecision.PASSED_SCREENING
    return decision, tray_for(decision), reasons
