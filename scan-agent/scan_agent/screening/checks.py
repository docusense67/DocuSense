"""Screening checks (S-03): headings, fields, layout, seals, fonts, alignment, unexpected
text and altered regions, measured against the document type's reference profile.

Severity scale (every indicator): severity = measured deviation / (2 x the profile's
tolerance for it), clipped to 0..1. So 0.5 means "exactly at the tolerance the profile
states" and 1.0 "twice the tolerance or more". Indicators are reported from 0.25 up (half the
tolerance) so the backend can tune its own threshold below the 0.5 default; whether any of
this matters is the backend's decision (backend/app/services/intake.py). Missing elements
have a fixed severity from the profile (`missing_severity`, default 0.8 for headings, 0.7 for
a field label, 0.3 for a blank value: blank fields are common on genuine forms).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from scan_agent.config import MM_TO_PX

from .measure import TextMetrics, match_template, text_metrics, tone_anomalies, tone_map
from .page import Box, Match, Page, Word, group_segments, union
from .profile import FieldSpec, Profile, Zone, load_profiles

EMIT_MIN = 0.25
MAX_PER_CODE = 5           # report at most this many indicators of one code (largest first)
UNEXPECTED_MIN_CONF = 60.0
LEARNED_MIN_REFS = 3       # learned positions replace profile zones from 3 references up


def _clip(value: float) -> float:
    return float(min(1.0, max(0.0, value)))


@dataclass
class Element:
    key: str                      # heading:<id> | label:<field>
    zone: Zone
    expected: tuple[float, float]  # px, the anchor point where the text should be
    tolerance_mm: float
    match: Match | None
    anchor: str = "center"
    distance_mm: float = 0.0

    @property
    def found_at(self) -> tuple[float, float]:
        assert self.match is not None
        if self.anchor == "left":
            x0, y0, _, y1 = self.match.box
            return float(x0), (y0 + y1) / 2
        return self.match.center

    def expected_for_match(self, page_width: int) -> tuple[float, float]:
        """Where the matched part of the phrase should be: a partly read heading is
        compared with the same part of its zone."""
        if self.match is None or self.anchor == "left":
            return self.expected
        s0, s1 = self.match.span
        return (self.expected[0] + self.zone[2] * page_width * ((s0 + s1) / 2 - 0.5),
                self.expected[1])


def _anchor_point(page: Page, zone: Zone, anchor: str) -> tuple[float, float]:
    cx, cy = page.zone_center(zone)
    return (zone[0] * page.width, cy) if anchor == "left" else (cx, cy)


@dataclass
class Analysis:
    checks: dict
    indicators: list[dict]
    fields: dict
    positions: dict[str, tuple[float, float]] = field(default_factory=dict)


class _Transform:
    """Expected -> found page position (similarity transform): absorbs a page that is
    shifted, slightly rotated or printed at a slightly different scale, so position checks
    measure the layout relative to the rest of the document."""

    def __init__(self, matrix: np.ndarray | None = None):
        self.m = np.array([[1, 0, 0], [0, 1, 0]], np.float64) if matrix is None else matrix
        self.page_angle: float | None = None  # residual page rotation, even if not applied

    def __call__(self, xy: tuple[float, float]) -> tuple[float, float]:
        x, y = xy
        return (self.m[0, 0] * x + self.m[0, 1] * y + self.m[0, 2],
                self.m[1, 0] * x + self.m[1, 1] * y + self.m[1, 2])

    @property
    def angle(self) -> float:
        if self.page_angle is not None:
            return self.page_angle
        return float(np.arctan2(self.m[1, 0], self.m[0, 0]))

    def shift_of(self, xy: tuple[float, float]) -> tuple[float, float]:
        tx, ty = self(xy)
        return tx - xy[0], ty - xy[1]


def _estimate(elements: list[Element], width: int) -> _Transform:
    """A plain shift (median displacement) unless a similarity transform (RANSAC) puts
    more of the found elements within their tolerance: a few elements printed slightly
    off must not tilt or scale the whole page model."""
    found = [e for e in elements if e.match]
    if not found:
        return _Transform()
    src = np.array([e.expected_for_match(width) for e in found], np.float64)
    dst = np.array([e.found_at for e in found], np.float64)
    tol = np.array([e.tolerance_mm for e in found]) * MM_TO_PX
    dx, dy = np.median(dst - src, axis=0)
    best = _Transform(np.array([[1, 0, dx], [0, 1, dy]], np.float64))

    def inliers(t: _Transform) -> int:
        moved = np.array([t(tuple(p)) for p in src])
        return int((np.hypot(*(moved - dst).T) <= tol).sum())

    if len(found) >= 4:
        matrix, _ = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC,
                                                ransacReprojThreshold=3.0 * MM_TO_PX)
        if matrix is not None:
            scale = float(np.hypot(matrix[0, 0], matrix[1, 0]))
            angle = float(np.degrees(np.arctan2(matrix[1, 0], matrix[0, 0])))
            candidate = _Transform(matrix)
            if 0.95 < scale < 1.05 and abs(angle) < 2:
                if inliers(candidate) > inliers(best):
                    best = candidate
                else:  # the shift places the elements, but baselines still need the tilt
                    best.page_angle = float(np.radians(angle))
    return best


def _indicator(page: Page, code: str, severity: float, detail: str, box: Box) -> dict:
    return {"code": code, "severity": round(_clip(severity), 2), "detail": detail,
            "box": page.input_box(box)}


def _expected(page: Page, key: str, zone: Zone, tolerance: float, learned: dict | None,
              anchor: str = "center") -> tuple[tuple[float, float], float]:
    center = _anchor_point(page, zone, anchor)
    if learned:
        entry = learned["positions"].get(key)
        if entry and entry["n"] >= LEARNED_MIN_REFS:
            cx, cy = entry["center"]
            points = np.array(entry["points"]) * (page.width, page.height)
            spread = float(np.median(np.hypot(*(points - (cx * page.width, cy * page.height)).T)))
            return (cx * page.width, cy * page.height), max(tolerance, 3 * spread / MM_TO_PX)
    return center, tolerance


def _template(path) -> np.ndarray | None:
    key = (str(path), path.stat().st_mtime)
    if key not in _TEMPLATES:
        _TEMPLATES[key] = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    return _TEMPLATES[key]


_TEMPLATES: dict = {}


def analyze(page: Page, profile: Profile, store=None) -> Analysis:
    learned = store.learned(profile.doc_type, profile.variant) if store is not None else None
    indicators: list[dict] = []

    # 1. headings and field labels, wherever they are printed
    elements: list[Element] = []
    for heading in profile.headings:
        center, tol = _expected(page, f"heading:{heading.id}", heading.zone,
                                heading.tolerance_mm, learned)
        elements.append(Element(f"heading:{heading.id}", heading.zone, center, tol,
                                page.find(heading.text, near=center)))
    for spec in profile.fields:
        if spec.label and spec.label_zone:
            center, tol = _expected(page, f"label:{spec.name}", spec.label_zone,
                                    spec.tolerance_mm, learned, spec.label_anchor)
            radius = max(60.0, 8 * tol) * MM_TO_PX
            near = page.zone_center(spec.label_zone)
            elements.append(Element(f"label:{spec.name}", spec.label_zone, center, tol,
                                    page.find(spec.label, near=near, radius=radius),
                                    spec.label_anchor))
    by_key = {e.key: e for e in elements}
    transform = _estimate(elements, page.width)

    # 2. positions relative to the rest of the page
    position_scores = []
    for element in elements:
        if element.match is None:
            continue
        px, py = transform(element.expected_for_match(page.width))
        cx, cy = element.found_at
        element.distance_mm = float(np.hypot(cx - px, cy - py)) / MM_TO_PX
        tol = element.tolerance_mm
        position_scores.append(_clip(1 - max(0.0, element.distance_mm - tol) / (2 * tol)))
        severity = element.distance_mm / (2 * tol)
        if severity >= EMIT_MIN:
            kind, _, name = element.key.partition(":")
            indicators.append(_indicator(
                page, "field_out_of_position", severity,
                f"{'heading' if kind == 'heading' else 'field'} '{name}' "
                f"{element.distance_mm:.1f} mm from its expected position "
                f"(tolerance {tol:.1f} mm)", element.match.box))
    headings_found = 0
    for heading in profile.headings:
        element = by_key[f"heading:{heading.id}"]
        if element.match:
            headings_found += 1
        else:
            dx, dy = transform.shift_of(element.expected)
            indicators.append(_indicator(page, "missing_heading", heading.missing_severity,
                                         f"heading '{heading.id}' not found",
                                         page.zone_box(heading.zone, dx, dy)))

    # 3. field values (as printed) in their zones, which move with their labels
    used: set[int] = {id(w) for e in elements if e.match for w in e.match.words}
    values: dict[str, list[Word]] = {}
    value_boxes: list[Box] = []
    fields_found = 0
    fields_out: dict[str, dict] = {}
    for spec in profile.fields:
        label = by_key.get(f"label:{spec.name}")
        if label and label.match:
            # The value zone is placed relative to the profile's label zone (also when the
            # label's expected position was learned from references).
            px, py = _anchor_point(page, label.zone, label.anchor)
            sx, _ = label.expected_for_match(page.width)
            ex, ey = px + (sx - label.expected[0]), py
            fx, fy = label.found_at
            dx, dy = fx - ex, fy - ey
        else:
            dx, dy = transform.shift_of(page.zone_center(spec.value_zone))
        box = page.zone_box(spec.value_zone, dx, dy, pad_mm=1.0)
        value_boxes.append(box)
        words = [w for w in page.words_in(box) if id(w) not in used]
        if label is not None and label.match is None:
            indicators.append(_indicator(page, "missing_field", spec.missing_label_severity,
                                         f"field '{spec.name}' label not found",
                                         page.zone_box(label.zone, *transform.shift_of(
                                             label.expected))))
        elif not words:
            indicators.append(_indicator(page, "missing_field", spec.missing_value_severity,
                                         f"field '{spec.name}' value blank or unreadable", box))
        else:
            fields_found += 1
        if words:
            used.update(id(w) for w in words)
            values[spec.name] = words
            ordered = [w for seg in sorted(group_segments(words), key=lambda s: (s[0].y, s[0].x))
                       for w in seg]
            fields_out[spec.name] = {"value": " ".join(w.text for w in ordered),
                                     "confidence": round(min(w.conf for w in words) / 100, 2)}

    # 4. glyph metrics of the members of font and alignment groups
    metrics_cache: dict[str, TextMetrics | None] = {}

    def metrics(member: str) -> TextMetrics | None:
        if member not in metrics_cache:
            box = None
            if member.startswith(("label:", "heading:")):
                element = by_key.get(member)
                box = element.match.box if element and element.match else None
            elif member in values:
                box = union(values[member])
            metrics_cache[member] = text_metrics(page.gray, box) if box else None
        return metrics_cache[member]

    def member_box(member: str) -> Box:
        element = by_key.get(member)
        if element and element.match:
            return element.match.box
        return union(values[member])

    spreads = []
    for group in profile.font_groups:
        measured = {m: mt.height for m in group.members if (mt := metrics(m))}
        if len(measured) < 2:
            continue
        heights = np.array(list(measured.values()))
        spreads.append(float((heights.max() - heights.min()) / np.median(heights)))
        worst, worst_dev = None, 0.0
        for member, height in measured.items():
            others = [h for m, h in measured.items() if m != member]
            dev = abs(height / float(np.median(others)) - 1)
            if dev > worst_dev:
                worst, worst_dev = member, dev
        severity = worst_dev / (2 * group.tolerance)
        if worst and severity >= EMIT_MIN:
            indicators.append(_indicator(
                page, "font_inconsistent", severity,
                f"'{worst}' text height {worst_dev:.0%} off font group '{group.id}' "
                f"(tolerance {group.tolerance:.0%})", member_box(worst)))
    font_consistency = round(_clip(1 - float(np.mean(spreads))), 3) if spreads else None

    cos_a, sin_a = np.cos(transform.angle), np.sin(transform.angle)
    group_scores = []
    for group in profile.alignment:
        measured = {}
        for member in group.members:
            mt = metrics(member)
            if not mt:
                continue
            x0, _, x1, _ = member_box(member)
            x, y = ((x0 + x1) / 2, mt.baseline) if group.kind == "baseline" else (mt.left, 0)
            if group.kind == "baseline":  # undo the page rotation before comparing
                measured[member] = -x * sin_a + y * cos_a
            else:
                y = (member_box(member)[1] + member_box(member)[3]) / 2
                measured[member] = x * cos_a + y * sin_a
        if len(measured) < 2:
            continue
        worst, worst_dev = None, 0.0
        for member, value in measured.items():
            others = [v for m, v in measured.items() if m != member]
            dev = abs(value - float(np.median(others))) / MM_TO_PX
            if dev > worst_dev:
                worst, worst_dev = member, dev
        group_scores.append(_clip(1 - worst_dev / (3 * group.tolerance)))
        severity = worst_dev / (2 * group.tolerance)
        if worst and severity >= EMIT_MIN:
            what = "baseline" if group.kind == "baseline" else "left edge"
            indicators.append(_indicator(
                page, "alignment_off", severity,
                f"'{worst}' {what} {worst_dev:.1f} mm off group '{group.id}' "
                f"(tolerance {group.tolerance:.1f} mm)", member_box(worst)))
    alignment_score = round(float(np.mean(group_scores)), 3) if group_scores else None

    # 5. seals and logos (only those with a template: none = not measured)
    seal_scores = []
    for seal in profile.seals:
        path = store.template(profile.doc_type, seal.template) if store is not None else None
        if path is None and profile.path is not None:
            candidate = profile.path.parent / "templates" / seal.template
            path = candidate if candidate.exists() else None
        template = _template(path) if path is not None else None
        if template is None:
            continue
        dx, dy = transform.shift_of(page.zone_center(seal.zone))
        search = page.zone_box(seal.zone, dx, dy, pad_mm=seal.search_margin_mm)
        score, found_box = match_template(page.gray, template, search)
        seal_scores.append(score)
        severity = (1 - score) / (2 * (1 - seal.min_score))
        if severity >= EMIT_MIN:
            indicators.append(_indicator(
                page, "seal_logo_not_found", severity,
                f"seal/logo '{seal.id}' best match {score:.2f} (expected {seal.min_score:.2f}+)",
                page.zone_box(seal.zone, dx, dy)))
    seal_logo_score = round(float(np.mean(seal_scores)), 3) if seal_scores else None

    # 6. text where the form has none
    # Allowed: headings, labels, value zones, seals, and other printed text: where the
    # reference samples have text (from 3 references up), else the profile's text zones.
    gdx, gdy = transform.shift_of((page.width / 2, page.height / 2))
    allowed: list[Box] = list(value_boxes)
    allowed += [page.zone_box(e.zone, gdx, gdy, pad_mm=e.tolerance_mm) for e in elements]
    allowed += [page.zone_box(s.zone, gdx, gdy, pad_mm=s.search_margin_mm)
                for s in profile.seals]
    occupied = None
    if learned is not None and learned["count"] >= LEARNED_MIN_REFS:
        occupied = learned["occupancy"] > 0
    else:
        allowed += [page.zone_box(z, gdx, gdy, pad_mm=2.0) for z in profile.text_zones]

    def is_allowed(word: Word) -> bool:
        if any(x0 <= word.cx <= x1 and y0 <= word.cy <= y1 for x0, y0, x1, y1 in allowed):
            return True
        if occupied is None:
            return False
        gy, gx = occupied.shape  # share of the word's cells where references had text
        x0 = max(0, int((word.x - gdx) / page.width * gx))
        x1 = min(gx, int((word.x + word.w - gdx) / page.width * gx) + 1)
        y0 = max(0, int((word.y - gdy) / page.height * gy))
        y1 = min(gy, int((word.y + word.h - gdy) / page.height * gy) + 1)
        cells = occupied[y0:y1, x0:x1]
        return bool(cells.size and cells.mean() >= 0.5)

    stray = [w for w in page.words if id(w) not in used and w.conf >= UNEXPECTED_MIN_CONF
             and len(w.norm) >= 2 and not is_allowed(w)]
    found_stray = []
    for segment in group_segments(stray):
        chars = sum(len(w.norm) for w in segment)
        severity = chars / (2 * profile.unexpected_min_chars)
        if severity >= EMIT_MIN:
            found_stray.append(_indicator(
                page, "unexpected_text", severity,
                f"{chars} characters of text outside the form's text areas", union(segment)))
    indicators += sorted(found_stray, key=lambda i: -i["severity"])[:MAX_PER_CODE]

    # 7. paper tone: pasted, covered or retouched regions
    exclude = [page.zone_box(z, gdx, gdy, pad_mm=2.0) for z in profile.shaded_zones]
    exclude += [page.zone_box(s.zone, gdx, gdy, pad_mm=3.0) for s in profile.seals]
    tones = tone_map(page.gray, exclude)
    expected_tone = learned["tone_residual"] if learned is not None else None
    altered = []
    for anomaly in tone_anomalies(tones, profile.tone_tolerance, expected_tone):
        severity = anomaly.level / (2 * profile.tone_tolerance)
        if severity >= EMIT_MIN:
            x0, y0, x1, y1 = anomaly.box
            altered.append(_indicator(
                page, "altered_region", severity,
                f"paper tone differs by {anomaly.level:.0f} grey levels over "
                f"{(x1 - x0) / MM_TO_PX:.0f} x {(y1 - y0) / MM_TO_PX:.0f} mm", anomaly.box))
    indicators += sorted(altered, key=lambda i: -i["severity"])[:MAX_PER_CODE]

    checks = {
        "expected_headings_found": headings_found,
        "expected_headings_total": len(profile.headings),
        "expected_fields_found": fields_found,
        "expected_fields_total": len(profile.fields),
        "layout_score": round(float(np.mean(position_scores)), 3) if position_scores else 0.0,
        "seal_logo_score": seal_logo_score,
        "font_consistency": font_consistency,
        "alignment_score": alignment_score,
    }
    positions = {e.key: e.found_at for e in elements if e.match}
    return Analysis(checks, indicators, fields_out, positions)


def best_variant(page: Page, doc_type: str,
                 profiles: dict[str, list[Profile]] | None = None) -> Profile:
    from .classify import keyword_score  # noqa: PLC0415

    variants = (profiles or load_profiles()).get(doc_type)
    if not variants:
        raise ValueError(f"no profile for document type {doc_type!r}")
    if len(variants) == 1:
        return variants[0]
    return max(variants, key=lambda p: keyword_score(page, p))


def screen(image, doc_type: str, store=None,
           profiles: dict[str, list[Profile]] | None = None) -> dict:
    """{"checks", "indicators", "fields"} of `image` (ndarray or Page) checked as
    `doc_type`."""
    page = image if isinstance(image, Page) else Page(image)
    analysis = analyze(page, best_variant(page, doc_type, profiles), store)
    return {"checks": analysis.checks, "indicators": analysis.indicators,
            "fields": analysis.fields}


__all__ = ["Analysis", "FieldSpec", "analyze", "screen", "best_variant"]
