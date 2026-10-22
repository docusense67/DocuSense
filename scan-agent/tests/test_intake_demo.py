"""Intake demo driver (scan_agent/intake_demo.py): SAMPLE pages through the real station
cycle, the real screening and a fake backend that mirrors backend/app/api/intake.py."""

import numpy as np
import pytest

from scan_agent import __main__ as cli
from scan_agent import intake_demo
from scan_agent.intake_demo import PLAN, SampleCamera, build_pages
from scan_agent.screening import synthetic
from tests.intake_server import FakeIntakeServer


def test_pages_follow_the_plan_with_one_sample_learner():
    pages = build_pages(seed=4)
    assert [(p.kind, p.tamper, p.poor) for p in pages] == [k[:3] for k in PLAN]
    learners = {p.person for p in pages if p.kind in synthetic.BUILDERS}
    assert len(learners) == 1
    (first, middle, last), = learners
    assert last == "SAMPLE" and first in synthetic.FIRST and middle in synthetic.LAST
    assert {p.lrn for p in pages if p.person} == {pages[0].lrn}
    assert pages[0].lrn.startswith("999999") and len(pages[0].lrn) == 12
    assert pages[5].person is None  # the out-of-scope memo is about nobody

    again = build_pages(seed=4)
    assert all(np.array_equal(a.image, b.image) for a, b in zip(pages, again, strict=True))
    assert build_pages(seed=5)[0].person != pages[0].person


def test_count_takes_a_prefix_or_adds_clean_pages_about_other_learners():
    assert [p.kind for p in build_pages(3, seed=1)] == [k[0] for k in PLAN[:3]]
    pages = build_pages(9, seed=1)
    extra = pages[7:]
    assert [p.kind for p in extra] == ["psa_birth_certificate", "jhs_report_card"]
    assert all(p.tamper is None and not p.poor for p in extra)
    assert extra[0].person != pages[0].person and extra[0].person[2] == "SAMPLE"


def test_the_learner_is_printed_on_the_page_values():
    sample = synthetic.render("psa_birth_certificate", 3, person=("ANA", "CRUZ", "SAMPLE"))
    assert (sample.values["child_first_name"], sample.values["child_last_name"]) == \
        ("ANA", "SAMPLE")
    assert sample.values["mother_last_name"] == "CRUZ"  # the middle name is the mother's
    plain = synthetic.render("psa_birth_certificate", 3)
    # Only the learner changes: the rest of the seed's data and the layout stay the same.
    assert plain.values["registrar_name"] == sample.values["registrar_name"]
    card = synthetic.render("jhs_report_card", 3, person=("ANA", "CRUZ", "SAMPLE"),
                            lrn="999999000001")
    assert card.values["learner_name"] == "SAMPLE, ANA C." and card.values["lrn"] == "999999000001"


def test_camera_stand_in_measures_quality_with_the_real_code():
    pages = build_pages(seed=1)
    camera = SampleCamera(pages)
    clean = camera.capture(1)
    assert clean.quality["ok"] and clean.capture["source"] == "sample"
    poor = camera.capture(7)
    assert not poor.quality["ok"] and "too_dark" in poor.quality["issues"]
    assert poor.image.mean() > pages[6].image.mean()  # levelled, like a camera page


def test_demo_batch_through_real_screening_and_the_backend_api(capsys):
    server = FakeIntakeServer()
    code = intake_demo.main(["--key", "station-key", "--seed", "1"], http=server.client())
    out = capsys.readouterr().out
    assert code == 0, out

    assert len(server.batches) == 1 and server.batches[1]["ended"]
    docs = [server.docs[i] for i in sorted(server.docs)]
    assert [d["sequence"] for d in docs] == list(range(1, 8))
    assert all(d["routed"] and d["routed_tray"] == d["tray"] for d in docs)
    assert [d["decision"] for d in docs] == ["passed_screening"] * 4 + ["manual_review"] * 3
    assert "severe_indicator" in docs[4]["reasons"]
    assert "doc_type_unknown" in docs[5]["reasons"]
    assert "quality_not_ok" in docs[6]["reasons"]
    assert server.statuses[-1][0] == "ready"
    # The station's report: type read, decision, tray and reasons per page.
    assert "Batch complete: 7 sheet(s), 4 in Tray 1, 3 in Tray 2" in out
    assert "needs manual review" in out and "Tray 2" in out and "quality_not_ok" in out
    assert "authentic" not in out.lower() and "verified" not in out.lower()


def test_cli_dispatch_and_arguments(monkeypatch, capsys):
    monkeypatch.delenv("ADGVS_SERVER", raising=False)
    monkeypatch.delenv("ADGVS_STATION_KEY", raising=False)
    with pytest.raises(SystemExit) as e:
        cli.main(["intake-demo"])
    assert e.value.code == 2 and "--server and --key" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(["intake-demo", "--server", "https://x.example", "--key", "k", "--count", "0"])
