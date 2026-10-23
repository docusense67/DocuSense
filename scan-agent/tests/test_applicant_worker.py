"""Applicant intake worker: reading OCR text, layout measurements, the classifier with reference
samples, and the claim -> pages -> complete cycle for every job kind.

No network: the API is a fake. Tesseract runs only in the tests marked "real". All text is a
SAMPLE.
"""

import json

import cv2
import httpx
import numpy as np
import pytest

from scan_agent import applicant_worker as worker
from scan_agent.processing import applicant

TOR_TEXT = """SAMPLE COLLEGE OF ARTS AND SCIENCES
OFFICIAL TRANSCRIPT OF RECORDS
Student Name: SAMPLEREYES, LUZVIMINDA C.
Student No.: OLD-2020-0042
Program: Bachelor of Arts in Sample Studies
Course code descriptive title units grade remarks semester
Date Issued: June 3, 2025
Registrar"""

COE_TEXT = """SAMPLE INSTITUTE OF TECHNOLOGY
CERTIFICATE OF ENROLLMENT
This is to certify that the student named below is officially enrolled.
Name: SAMPLEREYES, LUZVIMINDA C.
School Year: 2025-2026
Course: BS Sample Engineering, year level 2, semester purpose registrar"""

LAYOUT = {"anchors": {"transcript": [0.3, 0.1]}, "median_word_height": 0.02,
          "median_word_gap": 0.01, "median_line_gap": 0.04, "line_count": 8}


def test_classifies_a_transcript_and_reads_its_fields():
    reading = applicant.build_reading([TOR_TEXT])
    assert reading["schema"] == 1 and reading["quality"]["ok"] is True
    assert reading["doc_type"]["predicted"] == applicant.TOR
    assert reading["doc_type"]["confidence"] > 0.75
    fields = {k: v["value"] for k, v in reading["fields"].items()}
    assert fields["student_name"] == "SAMPLEREYES, LUZVIMINDA C."
    assert fields["student_number"] == "OLD-2020-0042"
    assert fields["program"] == "Bachelor of Arts in Sample Studies"
    assert fields["date_issued"] == "June 3, 2025"
    assert fields["school_name"] == "SAMPLE COLLEGE OF ARTS AND SCIENCES"
    assert all(v["confidence"] is None for v in reading["fields"].values())  # not measured
    assert "layout" not in reading  # nothing measured: the backend says so


def test_classifies_a_certificate_of_enrollment():
    reading = applicant.build_reading([COE_TEXT])
    assert reading["doc_type"]["predicted"] == applicant.COE
    assert reading["fields"]["school_year"]["value"] == "2025-2026"


def test_other_or_unreadable_documents():
    receipt = applicant.build_reading(["OFFICIAL RECEIPT invoice total payment cash amount "
                                       "balance bill address order quantity price"])
    assert receipt["doc_type"]["predicted"] == applicant.UNKNOWN
    blank = applicant.build_reading([TOR_TEXT, "  ..  "])
    assert blank["quality"] == {"ok": False, "unreadable_pages": [2]}
    nothing = applicant.build_reading(["x"])
    assert nothing["doc_type"]["predicted"] == applicant.UNKNOWN
    assert nothing["doc_type"]["confidence"] == 0.0
    assert nothing["doc_type"]["model"] == applicant.SYNTHETIC_MODEL


# ---- classifier and reference samples ---------------------------------------------------------

def test_synthetic_model_reports_its_version_and_evaluation():
    block = applicant.classify(TOR_TEXT)
    assert block["model"] == "naive-bayes-synthetic-v1" and block["synthetic_only"] is True
    assert block["training_reference_ids"] == []
    assert block["evaluation"] == {"synthetic_test_count": 3, "synthetic_test_accuracy": 1.0,
                                   "held_out_count": 0, "held_out_accuracy": None}


def test_training_references_extend_the_corpus_and_held_out_ones_only_evaluate():
    odd_tor = "SCHOLASTIC LEDGER cumulative ledger entries sample sample ledger ledger ledger"
    before = applicant.classify(odd_tor)
    assert before["predicted"] == applicant.UNKNOWN  # too few known words
    refs = [{"id": 7, "class": applicant.TOR, "use": "training", "text": odd_tor * 3},
            {"id": 8, "class": applicant.TOR, "use": "held_out", "text": odd_tor},
            {"id": 9, "class": "unknown", "use": "training",
             "text": "parking ticket plate number vehicle violation fine"},
            {"id": 10, "class": "bogus", "use": "training", "text": "ignored"},
            {"id": 11, "class": applicant.COE, "use": "training", "text": "  "}]
    after = applicant.classify(odd_tor, refs)
    assert after["predicted"] == applicant.TOR
    assert after["synthetic_only"] is False and after["training_reference_ids"] == [7, 9]
    assert after["model"].startswith("naive-bayes-") and after["model"] != before["model"]
    assert after["evaluation"]["held_out_count"] == 1
    assert after["evaluation"]["held_out_accuracy"] == 1.0
    # Held-out samples alone never train: the model stays the synthetic one.
    held_only = applicant.classify(odd_tor, [refs[1]])
    assert held_only["model"] == applicant.SYNTHETIC_MODEL
    assert held_only["evaluation"]["held_out_count"] == 1


def test_the_schools_printed_labels_find_fields():
    text = "SAMPLE STATE UNIVERSITY\nNAME OF LEARNER: SAMPLEREYES, LUZ\nGWA: 1.75\n"
    plain = applicant.extract_fields(text)
    assert "gwa" not in plain
    found = applicant.extract_fields(text, {"student_name": ["Name of learner"],
                                            "gwa": ["GWA", "General weighted average"]})
    assert found["student_name"]["value"] == "SAMPLEREYES, LUZ"
    assert found["gwa"]["value"] == "1.75"


# ---- layout measurements ----------------------------------------------------------------------

def data(words):
    """A pytesseract image_to_data dict from (text, left, top, width, height, line) tuples."""
    out = {k: [] for k in ("text", "left", "top", "width", "height", "block_num", "par_num",
                           "line_num")}
    for text, left, top, width, height, line in words:
        for key, value in zip(("text", "left", "top", "width", "height", "block_num",
                               "par_num", "line_num"),
                              (text, left, top, width, height, 1, 1, line), strict=True):
            out[key].append(value)
    return out


def test_layout_is_measured_from_word_boxes():
    page = data([("OFFICIAL", 100, 100, 200, 40, 1), ("TRANSCRIPT", 320, 100, 300, 40, 1),
                 ("", 0, 0, 0, 0, 1), ("Name:", 100, 300, 120, 20, 2),
                 ("Transcript", 400, 300, 200, 20, 2)])
    layout = applicant.layout_from_data(page, 1000, 2000)
    assert layout["anchors"] == {"transcript": [0.32, 0.05], "name": [0.1, 0.15]}  # first only
    assert layout["line_count"] == 2
    assert layout["median_word_height"] == pytest.approx(0.015)  # median of 20/40 px heights
    assert layout["median_word_gap"] == pytest.approx(0.1)  # median of 20 and 180 px / 1000
    assert layout["median_line_gap"] == pytest.approx(0.1)  # line tops 100 -> 300 of 2000
    assert applicant.text_from_data(page) == "OFFICIAL TRANSCRIPT\nName: Transcript"
    empty = applicant.layout_from_data(data([]), 1000, 2000)
    assert empty == {"anchors": {}, "median_word_height": 0.0, "median_word_gap": 0.0,
                     "median_line_gap": 0.0, "line_count": 0}


def test_reading_carries_the_layout_of_every_page():
    reading = applicant.build_reading([TOR_TEXT, TOR_TEXT], [LAYOUT, LAYOUT])
    pages = reading["layout"]["pages"]
    assert [p["page"] for p in pages] == [1, 2] and pages[0]["anchors"] == LAYOUT["anchors"]


# ---- the worker against a fake API ------------------------------------------------------------

class FakeApi:
    """Just enough of the station routes of backend/app/api/applicant_intake.py and
    applicant_references.py."""

    def __init__(self, pages=1, job=True, complete_status=200, kind=None, extra=None):
        ok, jpeg = cv2.imencode(".jpg", np.full((60, 40), 255, np.uint8))
        assert ok
        self.page = jpeg.tobytes()
        job_body = {"lease_token": "lease-1", "pages": pages, **(extra or {})}
        if kind == "reference":
            job_body.update(kind="reference", reference_id=4)
        else:
            job_body["submission_id"] = 7
            if kind:
                job_body["kind"] = kind
        self.job = job_body if job else None
        self.complete_status = complete_status
        self.completed = None
        self.completed_path = None
        self.page_paths = []
        self.page_tokens = []
        self.claim_params = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Station-Key"] == "station-key"
        path = request.url.path
        if path.endswith("/claim"):
            self.claim_params = dict(request.url.params)
            return httpx.Response(200, json={"job": self.job})
        if "/pages/" in path:
            self.page_paths.append(path)
            self.page_tokens.append(request.headers.get("X-Lease-Token"))
            if path.endswith("/pages/9"):
                return httpx.Response(404, json={"detail": "Page not found"})
            return httpx.Response(200, content=self.page, headers={"content-type": "image/jpeg"})
        if path.endswith("/complete") or path.endswith("/second-pass"):
            self.completed_path = path
            self.completed = json.loads(request.content)
            return httpx.Response(self.complete_status, json={"status": "screened"})
        return httpx.Response(404)


def client(api):
    return httpx.Client(base_url="http://api.test", transport=httpx.MockTransport(api),
                        headers={"X-Station-Key": "station-key"})


def no_layout(image):
    return LAYOUT


def fake_second(image):
    return TOR_TEXT.lower(), LAYOUT


def run(api, ocr=lambda img: TOR_TEXT, **kw):
    return worker.run_once(client(api), ocr=ocr, measure=no_layout, second=fake_second, **kw)


def test_one_job_reads_every_page_and_sends_the_reading():
    api = FakeApi(pages=2)
    seen = []
    assert run(api, ocr=lambda img: seen.append(img.shape) or TOR_TEXT)
    assert len(seen) == 2 and api.page_tokens == ["lease-1", "lease-1"]
    assert api.claim_params == {"kinds": "first,second,reference"}
    assert api.completed_path == "/api/station/applicant-jobs/7/complete"
    assert api.completed["lease_token"] == "lease-1"
    assert api.completed["reading"]["doc_type"]["predicted"] == applicant.TOR
    assert api.completed["reading"]["layout"]["pages"][1]["page"] == 2
    assert "pass 1" in api.completed["reading"]["engine"]
    assert "error" not in api.completed


def test_the_job_brings_the_classifier_corpus_and_the_schools_labels():
    ref_text = "SCHOLASTIC LEDGER cumulative ledger entries ledger ledger ledger"
    api = FakeApi(kind="first", extra={
        "classifier": [{"id": 3, "class": applicant.TOR, "use": "training",
                        "text": ref_text * 3}],
        "field_labels": {"gwa": ["GWA"]}})
    assert run(api, ocr=lambda img: ref_text + "\nGWA: 1.50")
    reading = api.completed["reading"]
    assert reading["doc_type"]["training_reference_ids"] == [3]
    assert reading["fields"]["gwa"]["value"] == "1.50"


def test_second_pass_job_reads_again_and_answers_on_its_own_route():
    api = FakeApi(pages=2, kind="second")
    first_pass_calls = []
    assert run(api, ocr=lambda img: first_pass_calls.append(1) or TOR_TEXT)
    assert first_pass_calls == []  # independent reading: the first pass's OCR is not used
    assert api.completed_path == "/api/station/applicant-jobs/7/second-pass"
    assert api.page_paths == ["/api/station/applicant-jobs/7/pages/1",
                              "/api/station/applicant-jobs/7/pages/2"]
    assert "pass 2" in api.completed["reading"]["engine"]
    assert api.completed["reading"]["pages"][0]["ocr_text"] == TOR_TEXT.lower()


def test_reference_job_uses_the_reference_routes():
    api = FakeApi(kind="reference")
    assert run(api)
    assert api.page_paths == ["/api/station/applicant-references/4/pages/1"]
    assert api.completed_path == "/api/station/applicant-references/4/complete"
    assert api.completed["reading"]["layout"]["pages"][0]["anchors"] == LAYOUT["anchors"]


def test_kinds_can_be_limited():
    api = FakeApi()
    assert run(api, kinds="first")
    assert api.claim_params == {"kinds": "first"}


def test_nothing_to_do():
    api = FakeApi(job=False)
    assert run(api) is False
    assert api.completed is None


def test_a_page_that_cannot_be_read_is_reported_without_document_text():
    api = FakeApi(pages=1)

    def broken(image):
        raise RuntimeError("Tesseract process failed")

    assert run(api, ocr=broken)
    assert api.completed == {"lease_token": "lease-1",
                             "error": "RuntimeError: Tesseract process failed"}


def test_a_failed_second_pass_is_reported_on_its_route():
    api = FakeApi(kind="second")

    def broken(image):
        raise RuntimeError("Tesseract process failed")

    assert worker.run_once(client(api), measure=no_layout, second=broken)
    assert api.completed_path.endswith("/7/second-pass")
    assert api.completed["error"].startswith("RuntimeError")


def test_a_missing_page_is_an_error_not_a_partial_reading():
    api = FakeApi(pages=9)  # pages 1-8 exist, page 9 answers 404
    assert run(api)
    assert "reading" not in api.completed and api.completed["error"].startswith("HTTPStatusError")


def test_a_lost_lease_is_dropped_quietly():
    api = FakeApi(complete_status=409)
    assert run(api) is True


def rendered_transcript() -> np.ndarray:
    image = np.full((1400, 1654), 255, np.uint8)
    for i, line in enumerate(TOR_TEXT.splitlines()):
        cv2.putText(image, line[:58], (80, 150 + i * 120), cv2.FONT_HERSHEY_SIMPLEX, 1.4, 0, 3,
                    cv2.LINE_AA)
    ok, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 85])
    assert ok
    return worker.decode(jpeg.tobytes())


def test_real_ocr_reads_a_rendered_transcript():
    """Tesseract (installed locally and in CI) on a page the backend would store."""
    reading = applicant.build_reading([worker._default_ocr(rendered_transcript())])
    assert reading["doc_type"]["predicted"] == applicant.TOR
    assert reading["fields"]["student_number"]["value"] == "OLD-2020-0042"


def test_real_layout_and_second_pass_on_a_rendered_transcript():
    page = rendered_transcript()
    layout = worker._default_measure(page)
    assert "transcript" in layout["anchors"] and layout["line_count"] >= 6
    x, y = layout["anchors"]["transcript"]
    assert 0 < x < 0.5 and 0.1 < y < 0.3  # second printed line, left half
    text, second_layout = worker._default_second(page)
    assert "TRANSCRIPT" in text.upper()
    assert "transcript" in second_layout["anchors"]
    reading = applicant.build_reading([text], [second_layout])
    assert reading["doc_type"]["predicted"] == applicant.TOR


def test_decode_refuses_garbage():
    with pytest.raises(ValueError):
        worker.decode(b"not an image")


def test_cli_needs_a_station_key(monkeypatch):
    monkeypatch.delenv("ADGVS_STATION_KEY", raising=False)
    with pytest.raises(SystemExit):
        worker.main(["--server", "http://api.test"])
