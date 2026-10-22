"""Document type identification (S-02).

Raw evidence per type, 0..1:
  ocr     weighted share of the profile's keywords found by the full-page OCR (a keyword
          found outside its expected zone counts half); best layout variant of the type.
  layout  when every type has reference samples: the similarity score against them
          (references.similarity). raw = 0.65 * ocr + 0.35 * layout.

Confidence (calibrated): p(type) = sigmoid(a + b * raw + c * margin), margin = raw minus the
best other type's raw. a, b, c are fitted by logistic regression on labelled pages
(`fit_calibration`; packaged values in profiles/calibration.json were fitted on the
synthetic set, `python -m scan_agent screening-calibrate` refits them on real samples into
the reference store). On the pages it was fitted on, p is the observed share of correct
predictions at that p (Platt scaling).

predicted = the type with the highest p if p >= 0.5 (more likely right than wrong),
otherwise "unknown" with confidence 1 - max p. The backend applies its own confidence
threshold (default 0.75) on top: this cut only defines what "unknown" means.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .page import Page
from .profile import DOC_TYPES, PROFILES_DIR, UNKNOWN, Profile, load_profiles

OCR_WEIGHT = 0.65
DEFAULT_CALIBRATION = {"ocr": {"a": -6.0, "b": 9.0, "c": 6.0},
                       "ocr+refs": {"a": -7.0, "b": 10.0, "c": 6.0}}


def keyword_score(page: Page, profile: Profile) -> float:
    key = ("keywords", profile.key)
    if key in page.cache:
        return page.cache[key]
    total = sum(k.weight for k in profile.keywords)
    got = 0.0
    for keyword in profile.keywords:
        match = page.find(keyword.text)
        if match is None:
            continue
        if keyword.zone is None:
            got += keyword.weight
            continue
        x0, y0, x1, y1 = page.zone_box(keyword.zone)
        cx, cy = match.center
        got += keyword.weight * (1.0 if x0 <= cx <= x1 and y0 <= cy <= y1 else 0.5)
    score = got / total if total else 0.0
    page.cache[key] = score
    return score


def raw_scores(page: Page, store=None,
               profiles: dict[str, list[Profile]] | None = None) -> tuple[dict, str]:
    """({doc_type: raw score}, mode) where mode is "ocr" or "ocr+refs"."""
    from .references import similarity  # noqa: PLC0415

    profiles = profiles or load_profiles()
    ocr = {t: max((keyword_score(page, p) for p in profiles.get(t, [])), default=0.0)
           for t in DOC_TYPES}
    if store is not None and all(store.count(t) for t in DOC_TYPES):
        raw = {}
        for t in DOC_TYPES:
            sim = similarity(page, t, store, profiles)
            raw[t] = OCR_WEIGHT * ocr[t] + (1 - OCR_WEIGHT) * (sim["score"] if sim else 0.0)
        return raw, "ocr+refs"
    return ocr, "ocr"


def _sigmoid(z: float) -> float:
    return float(1 / (1 + np.exp(-np.clip(z, -30, 30))))


def calibration(store=None) -> dict:
    if store is not None and (own := store.calibration()):
        return own
    path = PROFILES_DIR / "calibration.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return DEFAULT_CALIBRATION


def probabilities(raw: dict[str, float], params: dict) -> dict[str, float]:
    out = {}
    for t, score in raw.items():
        others = [s for u, s in raw.items() if u != t]
        margin = score - max(others, default=0.0)
        out[t] = _sigmoid(params["a"] + params["b"] * score + params["c"] * margin)
    return out


def classify(image, store=None, profiles: dict[str, list[Profile]] | None = None) -> dict:
    """The contract's doc_type block: {"predicted", "confidence", "scores"}."""
    page = image if isinstance(image, Page) else Page(image)
    raw, mode = raw_scores(page, store, profiles)
    params = calibration(store)[mode]
    probs = probabilities(raw, params)
    best = max(probs, key=probs.get)  # type: ignore[arg-type]
    if probs[best] >= 0.5:
        predicted, confidence = best, probs[best]
    else:
        predicted, confidence = UNKNOWN, 1 - probs[best]
    page.cache["classify_raw"] = (raw, mode)
    return {"predicted": predicted, "confidence": round(confidence, 3),
            "scores": {t: round(p, 3) for t, p in probs.items()}}


def fit_calibration(samples: list[tuple[dict[str, float], str]], ridge: float = 0.1) -> dict:
    """Logistic regression (IRLS) of "this type is the truth" on (raw, margin) over every
    (page, type) pair. `samples`: (raw scores, true doc_type or "unknown"). The L2 ridge
    keeps the weights finite when the classes separate perfectly (as on synthetic pages),
    so confidences stay graded instead of jumping from 0 to 1."""
    rows, labels = [], []
    for raw, truth in samples:
        for t, score in raw.items():
            margin = score - max(s for u, s in raw.items() if u != t)
            rows.append((1.0, score, margin))
            labels.append(1.0 if t == truth else 0.0)
    x, y = np.array(rows), np.array(labels)
    w = np.zeros(3)
    for _ in range(50):
        p = 1 / (1 + np.exp(-np.clip(x @ w, -30, 30)))
        grad = x.T @ (p - y) + ridge * w
        hess = (x.T * (p * (1 - p))) @ x + ridge * np.eye(3)
        step = np.linalg.solve(hess, grad)
        w -= step
        if np.abs(step).max() < 1e-8:
            break
    return {"a": round(float(w[0]), 4), "b": round(float(w[1]), 4), "c": round(float(w[2]), 4),
            "n_pages": len(samples)}


def write_calibration(path: Path, fitted: dict[str, dict], note: str) -> None:
    current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else dict(
        DEFAULT_CALIBRATION)
    current.update(fitted)
    current["note"] = note
    path.write_text(json.dumps(current, indent=1) + "\n", encoding="utf-8", newline="")
