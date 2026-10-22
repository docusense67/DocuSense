"""Fast unit tests of the screening building blocks (no OCR)."""

import json
import random

import numpy as np
import pytest
from PIL import Image, ImageDraw

from scan_agent.config import MM_TO_PX
from scan_agent.screening import DOC_TYPES, Page, load_profiles
from scan_agent.screening.classify import fit_calibration, probabilities
from scan_agent.screening.measure import match_template, text_metrics, tone_anomalies, tone_map
from scan_agent.screening.page import Word
from scan_agent.screening.profile import PROFILES_DIR, parse_profile
from scan_agent.screening.references import layout_similarity, orb_similarity, signature
from scan_agent.screening.synthetic import BUILDERS, degrade, draw_layout, font, layout, render


def test_every_document_type_has_a_profile():
    profiles = load_profiles()
    assert set(profiles) == set(DOC_TYPES)
    for doc_type, variants in profiles.items():
        for profile in variants:
            assert profile.keywords and profile.headings and profile.fields, doc_type
            assert profile.font_groups and profile.alignment, doc_type


def test_report_card_table_expands_to_row_fields_and_header_headings():
    profile = load_profiles()["jhs_report_card"][0]
    names = {f.name for f in profile.fields}
    assert {"filipino", "mathematics", "mapeh", "general_average"} <= names
    assert {"learning_areas", "final_rating"} <= {h.id for h in profile.headings}
    assert profile.field("mathematics").label_anchor == "left"
    assert any(g.id == "grades_values" for g in profile.font_groups)


def _raw(**changes):
    raw = json.loads((PROFILES_DIR / "good_moral_certificate.json").read_text(encoding="utf-8"))
    raw.update(changes)
    return raw


@pytest.mark.parametrize("changes, message", [
    ({"schema": 2}, "schema"),
    ({"doc_type": "diploma"}, "doc_type"),
    ({"keywords": []}, "keywords"),
    ({"font_groups": [{"id": "x", "members": ["no_such_field"]}]}, "unknown members"),
    ({"seals": [{"id": "s", "template": "s.png", "zone": [0.9, 0.9, 0.5, 0.05]}]}, "outside"),
])
def test_profile_validation_rejects_broken_profiles(changes, message):
    with pytest.raises(ValueError, match=message):
        parse_profile(_raw(**changes))


def test_text_metrics_compare_heights_and_baselines_across_words():
    image = Image.new("L", (1600, 400), 240)
    draw = ImageDraw.Draw(image)
    draw.text((50, 150), "Maria Ignacia", font=font(42), fill=20, anchor="ls")
    draw.text((700, 150), "DELA CRUZ 2010", font=font(63), fill=20, anchor="ls")
    gray = np.array(image)
    small = text_metrics(gray, (40, 100, 650, 170))
    large = text_metrics(gray, (690, 70, 1590, 175))
    assert small and large
    assert 1.35 < large.height / small.height < 1.65
    assert abs(small.baseline - 150) <= 2 and abs(large.baseline - 150) <= 2
    assert abs(small.left - 50) <= 4


def test_tone_map_finds_a_pasted_patch_and_nothing_on_the_clean_page():
    lay = layout("good_moral_certificate", 3)
    clean = draw_layout(lay, paper=236)
    pasted = clean.copy()
    y0, y1, x0, x1 = (round(v * MM_TO_PX) for v in (108, 122, 40, 170))
    pasted[y0:y1, x0:x1] = 251
    for image, expect in ((clean, False), (pasted, True)):
        noisy = degrade(image, random.Random(1))
        found = tone_anomalies(tone_map(noisy, []), tolerance=6.0)
        strong = [a for a in found if a.level / 12 >= 0.5]
        assert bool(strong) == expect
        if expect:
            bx0, by0, bx1, by1 = strong[0].box
            assert bx0 < x1 and x0 < bx1 and by0 < y1 and y0 < by1


def test_seal_template_matches_the_seal_and_not_its_absence():
    lay = layout("psa_birth_certificate", 1)
    seal = next(s for s in lay.seals if s.templated)
    x0, y0 = round((seal.cx - seal.r - 1) * MM_TO_PX), round((seal.cy - seal.r - 1) * MM_TO_PX)
    x1, y1 = round((seal.cx + seal.r + 1) * MM_TO_PX), round((seal.cy + seal.r + 1) * MM_TO_PX)
    template = render("psa_birth_certificate", 2, clean_capture=True).image[y0:y1, x0:x1]
    search = (x0 - 120, y0 - 120, x1 + 120, y1 + 120)
    present, _ = match_template(render("psa_birth_certificate", 5).image, template, search)
    missing, _ = match_template(render("psa_birth_certificate", 5, "seal_missing").image,
                                template, search)
    assert present > 0.7 and missing < 0.3


def _page_with(words):
    page = Page(np.full((3508, 2480), 240, np.uint8))
    page._words = [Word(t, 95.0, x, y, w, 40) for t, x, y, w in words]
    return page


def test_phrase_lookup():
    page = _page_with([("CERTIFICATE", 100, 100, 500), ("OF", 620, 100, 80),
                       ("COMPLE", 720, 100, 300), ("PAGE", 100, 400, 200),
                       ("(First)", 300, 900, 150), ("(First)", 300, 1500, 150)])
    partial = page.find("CERTIFICATE OF COMPLETION")
    assert partial and partial.span[0] == 0 and partial.span[1] < 0.9  # read in part
    assert page.find("CERTIFICATE OF RECOGNITION") is None  # same shape, other title
    assert page.find("AGE") is None  # short phrases must match exactly
    near = page.find("(First)", near=(370, 1480), radius=500)
    assert near and near.words[0].y == 1500


def test_layout_and_orb_similarity_separate_types():
    profiles = load_profiles()
    psa = profiles["psa_birth_certificate"][0]
    a = signature(Page(render("psa_birth_certificate", 1).image), psa)
    b = signature(Page(render("psa_birth_certificate", 2).image), psa)
    c = signature(Page(render("jhs_report_card", 1).image), psa)
    assert layout_similarity(a, b) > 0.9 > 0.7 > layout_similarity(a, c)
    assert orb_similarity(a, b) > 0.5 > 0.1 > orb_similarity(a, c)


def test_calibration_fit_ranks_the_true_type_first():
    rng = random.Random(3)
    samples = []
    for _ in range(40):
        truth = rng.choice(DOC_TYPES + ("unknown",))
        raw = {t: rng.uniform(0.0, 0.3) for t in DOC_TYPES}
        if truth != "unknown":
            raw[truth] = rng.uniform(0.6, 1.0)
        samples.append((raw, truth))
    params = fit_calibration(samples)
    assert params["b"] > 0 and params["n_pages"] == 40
    probs = probabilities({"psa_birth_certificate": 0.9, "jhs_report_card": 0.1,
                           "good_moral_certificate": 0.1, "jhs_completion_certificate": 0.0},
                          params)
    assert probs["psa_birth_certificate"] > 0.8 > 0.2 > probs["jhs_report_card"]
    low = probabilities({t: 0.15 for t in DOC_TYPES}, params)
    assert max(low.values()) < 0.5  # nothing convincing: "unknown"


def test_synthetic_pages_are_marked_sample():
    lay = layout("jhs_report_card", 1)
    page = draw_layout(lay, paper=240)
    assert ((page >= 220) & (page <= 228)).sum() > 20000  # the light SAMPLE watermark
    assert all(doc_type in BUILDERS for doc_type in DOC_TYPES)
