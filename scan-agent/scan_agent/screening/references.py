"""Reference-sample store, ORB + layout features, similarity scoring and enrollment.

Store layout (outside the repository: the samples are minors' records):

    <root>/                                  ADGVS_SCREENING_REFS, default ~/.adgvs/screening-refs
      calibration.json                       optional: confidence calibration refit on samples
      <doc_type>/
        index.json                           {"schema": 1, "references": [{id, variant, ...}]}
        images/<id>.png                      the sample, field values whited out, long side 1200 px
        features/<id>.npz                    ORB keypoints + descriptors, layout signature
        features/<id>.json                   measured positions, text occupancy, paper tone
        templates/<seal>.png                 seal / logo templates named in the profile

`python -m scan_agent screening-enroll --type <t> <images or folders...>` adds samples; the
id is a hash of the page, so enrolling the same file again replaces it. Features are computed
with every profile value zone whited out, so they describe the form, not the person, and a
stored image holds no field values (text elsewhere, e.g. inside a body paragraph, may remain:
keep the store on the station's encrypted / access-controlled disk).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .measure import crop_ink, tone_map
from .page import Page
from .profile import DOC_TYPES, Profile, load_profiles

STORE_ENV = "ADGVS_SCREENING_REFS"
FEATURE_LONG_PX = 1000     # ORB runs at ~85 DPI: form structure, not glyph detail
ORB_FEATURES = 1000
LAYOUT_GRID = (24, 34)     # ink-density signature cells (short side, long side)
OCCUPANCY_GRID = (42, 60)  # 5 mm cells on A4: where references have printed text
# ORB inlier share (of the smaller keypoint set) that counts as a perfect match. Same-form
# synthetic pairs reach 0.15-0.45 at ~85 DPI; different forms stay under 0.03.
ORB_FULL_SHARE = 0.25
SIMILARITY_TOP = 3         # score = mean of the best 3 references
ORB_CANDIDATES = 5         # ORB is run on the 5 references with the closest layout only
METHOD = "orb+layout"


def default_root() -> Path:
    return Path(os.environ.get(STORE_ENV) or Path.home() / ".adgvs" / "screening-refs")


@dataclass
class Signature:
    keypoints: np.ndarray    # (N, 2) float32, fractions of width / height
    descriptors: np.ndarray  # (N, 32) uint8
    layout: np.ndarray       # z-scored ink density, LAYOUT_GRID cells
    aspect: float            # height / width


@dataclass
class Reference:
    id: str
    variant: str
    signature: Signature
    measured: dict


def masked_gray(page: Page, profile: Profile) -> np.ndarray:
    gray = page.gray.copy()
    for spec in profile.fields:
        x0, y0, x1, y1 = page.zone_box(spec.value_zone, pad_mm=1.0)
        gray[y0:y1, x0:x1] = 255
    return gray


def signature(page: Page, profile: Profile) -> Signature:
    key = ("signature", profile.key)
    if key in page.cache:
        return page.cache[key]
    gray = masked_gray(page, profile)
    scale = FEATURE_LONG_PX / max(gray.shape)
    small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    orb = cv2.ORB_create(nfeatures=ORB_FEATURES, fastThreshold=12)
    kps, desc = orb.detectAndCompute(small, None)
    h, w = small.shape
    points = np.array([(k.pt[0] / w, k.pt[1] / h) for k in kps], np.float32).reshape(-1, 2)
    if desc is None:
        desc = np.zeros((0, 32), np.uint8)
    ink = (small < cv2.threshold(small, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[0]
           ).astype(np.float32)
    grid = LAYOUT_GRID if h >= w else LAYOUT_GRID[::-1]
    density = cv2.resize(ink, grid, interpolation=cv2.INTER_AREA)
    density = cv2.GaussianBlur(density, (3, 3), 0).ravel()
    std = float(density.std())
    layout = ((density - density.mean()) / std if std > 1e-6 else density * 0).astype(np.float32)
    result = Signature(points, desc, layout, h / w)
    page.cache[key] = result
    return result


def layout_similarity(a: Signature, b: Signature) -> float:
    if abs(a.aspect - b.aspect) > 0.12 or a.layout.shape != b.layout.shape:
        return 0.0
    return max(0.0, float(np.mean(a.layout * b.layout)))


_matcher = cv2.BFMatcher(cv2.NORM_HAMMING)


def orb_similarity(query: Signature, ref: Signature) -> float:
    """Share of ORB matches (ratio test) that agree on one near-identity similarity
    transform. The page is already perspective-corrected, so a matching form must line up:
    large rotations, scalings or shifts count as no match."""
    if abs(query.aspect - ref.aspect) > 0.12 or len(query.descriptors) < 10 or len(
            ref.descriptors) < 10:
        return 0.0
    pairs = _matcher.knnMatch(query.descriptors, ref.descriptors, k=2)
    good = [p[0] for p in pairs if len(p) == 2 and p[0].distance < 0.75 * p[1].distance]
    if len(good) < 8:
        return 0.0
    if query.aspect >= 1:  # (width, height) of the feature image
        size = np.array([FEATURE_LONG_PX / query.aspect, FEATURE_LONG_PX], np.float32)
    else:
        size = np.array([FEATURE_LONG_PX, FEATURE_LONG_PX * query.aspect], np.float32)
    src = ref.keypoints[[m.trainIdx for m in good]] * size
    dst = query.keypoints[[m.queryIdx for m in good]] * size
    matrix, inliers = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC,
                                                  ransacReprojThreshold=6.0)
    if matrix is None or inliers is None:
        return 0.0
    scale = float(np.hypot(matrix[0, 0], matrix[1, 0]))
    angle = float(np.degrees(np.arctan2(matrix[1, 0], matrix[0, 0])))
    shift = float(np.hypot(matrix[0, 2], matrix[1, 2])) / FEATURE_LONG_PX
    if not (0.9 < scale < 1.1) or abs(angle) > 4 or shift > 0.08:
        return 0.0
    share = int(inliers.sum()) / min(len(query.descriptors), len(ref.descriptors))
    return min(1.0, share / ORB_FULL_SHARE)


class ReferenceStore:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        self._cache: dict[str, tuple[float, list[Reference]]] = {}
        self._learned: dict[tuple, dict] = {}

    @classmethod
    def default(cls) -> ReferenceStore | None:
        root = default_root()
        return cls(root) if root.is_dir() else None

    def type_dir(self, doc_type: str) -> Path:
        if doc_type not in DOC_TYPES:
            raise ValueError(f"unknown document type {doc_type!r}")
        return self.root / doc_type

    def _index_path(self, doc_type: str) -> Path:
        return self.type_dir(doc_type) / "index.json"

    def index(self, doc_type: str) -> dict:
        path = self._index_path(doc_type)
        if not path.exists():
            return {"schema": 1, "references": []}
        return json.loads(path.read_text(encoding="utf-8"))

    def references(self, doc_type: str) -> list[Reference]:
        path = self._index_path(doc_type)
        if not path.exists():
            return []
        mtime = path.stat().st_mtime
        cached = self._cache.get(doc_type)
        if cached and cached[0] == mtime:
            return cached[1]
        refs = []
        features = self.type_dir(doc_type) / "features"
        for entry in self.index(doc_type)["references"]:
            data = np.load(features / f"{entry['id']}.npz")
            sig = Signature(data["keypoints"], data["descriptors"], data["layout"],
                            float(data["aspect"]))
            measured = json.loads((features / f"{entry['id']}.json").read_text(encoding="utf-8"))
            refs.append(Reference(entry["id"], entry["variant"], sig, measured))
        self._cache[doc_type] = (mtime, refs)
        return refs

    def count(self, doc_type: str) -> int:
        return len(self.index(doc_type)["references"])

    def template(self, doc_type: str, name: str) -> Path | None:
        path = self.type_dir(doc_type) / "templates" / name
        return path if path.exists() else None

    def calibration(self) -> dict | None:
        path = self.root / "calibration.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def learned(self, doc_type: str, variant: str) -> dict | None:
        """Aggregates over the variant's references: median measured position and spread of
        each heading / label, text occupancy, and the mean paper-tone residual."""
        refs = [r for r in self.references(doc_type) if r.variant == variant]
        if not refs:
            return None
        key = (doc_type, variant, self._cache[doc_type][0], len(refs))
        if key in self._learned:
            return self._learned[key]
        positions: dict[str, list] = {}
        for ref in refs:
            for name, xy in ref.measured.get("positions", {}).items():
                positions.setdefault(name, []).append(xy)
        learned_pos = {}
        for name, points in positions.items():
            arr = np.array(points, np.float64)
            center = np.median(arr, axis=0)
            learned_pos[name] = {"center": center.tolist(), "n": len(points),
                                 "points": arr.tolist()}
        occupancy = np.zeros(OCCUPANCY_GRID[::-1], np.float32)
        tones = []
        for ref in refs:
            grid = np.array(ref.measured.get("occupancy", []), np.float32)
            if grid.shape == occupancy.shape:
                occupancy += grid
            tone = np.array(ref.measured.get("tone_residual", []), np.float32)
            if tone.ndim == 2 and tone.size:
                tones.append(tone)
        tone_mean = None
        if tones:
            shape = tones[0].shape
            tone_mean = np.mean([cv2.resize(t, shape[::-1]) if t.shape != shape else t
                                 for t in tones], axis=0)
        result = {"count": len(refs), "positions": learned_pos,
                  "occupancy": occupancy / len(refs), "tone_residual": tone_mean}
        self._learned[key] = result
        return result


def similarity(page: Page, doc_type: str, store: ReferenceStore | None,
               profiles: dict[str, list[Profile]] | None = None) -> dict | None:
    """{"score", "reference_count", "method"} against the stored references of `doc_type`,
    or None when there are none (the block is then omitted: not measured)."""
    if store is None:
        return None
    refs = store.references(doc_type)
    if not refs:
        return None
    profiles = profiles or load_profiles()
    variants = {p.variant: p for p in profiles.get(doc_type, [])}
    scored = []
    for ref in refs:
        profile = variants.get(ref.variant) or next(iter(variants.values()))
        query = signature(page, profile)
        scored.append((layout_similarity(query, ref.signature), ref, query))
    scored.sort(key=lambda item: item[0], reverse=True)
    combined = []
    for layout, ref, query in scored[:ORB_CANDIDATES]:
        combined.append(0.5 * orb_similarity(query, ref.signature) + 0.5 * layout)
    combined.sort(reverse=True)
    top = combined[:SIMILARITY_TOP]
    return {"score": round(float(np.mean(top)), 3), "reference_count": len(refs),
            "method": METHOD}


# -- enrollment -----------------------------------------------------------------------
def page_id(page: Page) -> str:
    return hashlib.sha256(page.gray.tobytes()).hexdigest()[:16]


def occupancy(page: Page) -> np.ndarray:
    grid = np.zeros(OCCUPANCY_GRID[::-1], np.float32)
    gx, gy = OCCUPANCY_GRID
    for w in page.words:
        if w.conf < 30:
            continue
        x0, x1 = int(w.x / page.width * gx), int((w.x + w.w) / page.width * gx)
        y0, y1 = int(w.y / page.height * gy), int((w.y + w.h) / page.height * gy)
        grid[max(0, y0):min(gy, y1 + 1), max(0, x0):min(gx, x1 + 1)] = 1.0
    return grid


def tone_exclusions(page: Page, profile: Profile) -> list:
    boxes = [page.zone_box(z, pad_mm=2.0) for z in profile.shaded_zones]
    boxes += [page.zone_box(s.zone, pad_mm=s.search_margin_mm) for s in profile.seals]
    return boxes


def enroll(store: ReferenceStore, doc_type: str, images: list[np.ndarray],
           keep_images: bool = True, extract_seals: bool = True,
           profiles: dict[str, list[Profile]] | None = None) -> list[dict]:
    """Add reference samples of `doc_type`. Returns one report per image (no personal
    data: ids, the variant matched and what was found)."""
    from .checks import analyze  # noqa: PLC0415 (checks imports this module)
    from .classify import keyword_score  # noqa: PLC0415

    profiles = profiles or load_profiles()
    variants = profiles.get(doc_type)
    if not variants:
        raise ValueError(f"no profile for {doc_type!r}")
    folder = store.type_dir(doc_type)
    (folder / "features").mkdir(parents=True, exist_ok=True)
    index = store.index(doc_type)
    entries = {e["id"]: e for e in index["references"]}
    reports = []
    for image in images:
        page = Page(image)
        profile = max(variants, key=lambda p: keyword_score(page, p))
        ident = page_id(page)
        sig = signature(page, profile)
        analysis = analyze(page, profile, store=None)
        positions = {k: [round(x / page.width, 5), round(y / page.height, 5)]
                     for k, (x, y) in analysis.positions.items()}
        tones = tone_map(page.gray, tone_exclusions(page, profile))
        measured = {"positions": positions, "occupancy": occupancy(page).astype(int).tolist(),
                    "tone_residual": np.round(tones.residual, 1).tolist()}
        np.savez_compressed(folder / "features" / f"{ident}.npz", keypoints=sig.keypoints,
                            descriptors=sig.descriptors, layout=sig.layout,
                            aspect=np.float32(sig.aspect))
        (folder / "features" / f"{ident}.json").write_text(json.dumps(measured), encoding="utf-8",
                                                          newline="")
        image_name = None
        if keep_images:
            (folder / "images").mkdir(exist_ok=True)
            masked = masked_gray(page, profile)
            scale = 1200 / max(masked.shape)
            small = cv2.resize(masked, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            image_name = f"images/{ident}.png"
            cv2.imwrite(str(folder / image_name), small)
        made = []
        if extract_seals:
            for seal in profile.seals:
                target = folder / "templates" / seal.template
                if target.exists():
                    continue
                crop = crop_ink(page.gray, page.zone_box(seal.zone, pad_mm=3.0))
                if crop is not None:
                    target.parent.mkdir(exist_ok=True)
                    cv2.imwrite(str(target), crop)
                    made.append(seal.id)
        entries[ident] = {"id": ident, "variant": profile.variant, "image": image_name,
                          "enrolled_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        reports.append({"id": ident, "variant": profile.variant,
                        "keyword_score": round(keyword_score(page, profile), 3),
                        "headings_found": analysis.checks["expected_headings_found"],
                        "headings_total": analysis.checks["expected_headings_total"],
                        "fields_found": analysis.checks["expected_fields_found"],
                        "fields_total": analysis.checks["expected_fields_total"],
                        "templates_created": made})
    index["references"] = sorted(entries.values(), key=lambda e: e["id"])
    path = store._index_path(doc_type)
    path.write_text(json.dumps(index, indent=1), encoding="utf-8", newline="")
    store._cache.pop(doc_type, None)
    return reports


def remove(store: ReferenceStore, doc_type: str, ident: str) -> bool:
    index = store.index(doc_type)
    kept = [e for e in index["references"] if e["id"] != ident]
    if len(kept) == len(index["references"]):
        return False
    folder = store.type_dir(doc_type)
    for path in (folder / "features" / f"{ident}.npz", folder / "features" / f"{ident}.json",
                 folder / "images" / f"{ident}.png"):
        path.unlink(missing_ok=True)
    index["references"] = kept
    store._index_path(doc_type).write_text(json.dumps(index, indent=1), encoding="utf-8",
                                          newline="")
    return True

