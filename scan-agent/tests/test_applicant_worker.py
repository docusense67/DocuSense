"""Applicant intake worker: reading OCR text, and the claim -> pages -> complete cycle.

No Tesseract and no network: the OCR function and the API are fakes. All text is a SAMPLE.
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
    assert nothing["doc_type"] == {"predicted": applicant.UNKNOWN, "confidence": 0.0,
                                   "model": "naive-bayes-synthetic-v1"}


class FakeApi:
    """Just enough of the station routes of backend/app/api/applicant_intake.py."""

    def __init__(self, pages=1, job=True, complete_status=200):
        ok, jpeg = cv2.imencode(".jpg", np.full((60, 40), 255, np.uint8))
        assert ok
        self.page = jpeg.tobytes()
        self.job = {"submission_id": 7, "lease_token": "lease-1", "pages": pages} if job else None
        self.complete_status = complete_status
        self.completed = None
        self.page_tokens = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Station-Key"] == "station-key"
        path = request.url.path
        if path.endswith("/claim"):
            return httpx.Response(200, json={"job": self.job})
        if "/pages/" in path:
            self.page_tokens.append(request.headers.get("X-Lease-Token"))
            if path.endswith("/pages/9"):
                return httpx.Response(404, json={"detail": "Page not found"})
            return httpx.Response(200, content=self.page, headers={"content-type": "image/jpeg"})
        if path.endswith("/complete"):
            self.completed = json.loads(request.content)
            return httpx.Response(self.complete_status, json={"status": "screened"})
        return httpx.Response(404)


def client(api):
    return httpx.Client(base_url="http://api.test", transport=httpx.MockTransport(api),
                        headers={"X-Station-Key": "station-key"})


def test_one_job_reads_every_page_and_sends_the_reading():
    api = FakeApi(pages=2)
    seen = []
    assert worker.run_once(client(api), ocr=lambda img: seen.append(img.shape) or TOR_TEXT)
    assert len(seen) == 2 and api.page_tokens == ["lease-1", "lease-1"]
    assert api.completed["lease_token"] == "lease-1"
    assert api.completed["reading"]["doc_type"]["predicted"] == applicant.TOR
    assert "error" not in api.completed


def test_nothing_to_do():
    api = FakeApi(job=False)
    assert worker.run_once(client(api), ocr=lambda img: TOR_TEXT) is False
    assert api.completed is None


def test_a_page_that_cannot_be_read_is_reported_without_document_text():
    api = FakeApi(pages=1)

    def broken(image):
        raise RuntimeError("Tesseract process failed")

    assert worker.run_once(client(api), ocr=broken)
    assert api.completed == {"lease_token": "lease-1",
                             "error": "RuntimeError: Tesseract process failed"}


def test_a_missing_page_is_an_error_not_a_partial_reading():
    api = FakeApi(pages=9)  # pages 1-8 exist, page 9 answers 404
    assert worker.run_once(client(api), ocr=lambda img: TOR_TEXT)
    assert "reading" not in api.completed and api.completed["error"].startswith("HTTPStatusError")


def test_a_lost_lease_is_dropped_quietly():
    api = FakeApi(complete_status=409)
    assert worker.run_once(client(api), ocr=lambda img: TOR_TEXT) is True


def test_real_ocr_reads_a_rendered_transcript():
    """Tesseract (installed locally and in CI) on a page the backend would store."""
    image = np.full((1400, 1654), 255, np.uint8)
    for i, line in enumerate(TOR_TEXT.splitlines()):
        cv2.putText(image, line[:58], (80, 150 + i * 120), cv2.FONT_HERSHEY_SIMPLEX, 1.4, 0, 3,
                    cv2.LINE_AA)
    ok, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 85])
    assert ok
    reading = applicant.build_reading([worker._default_ocr(worker.decode(jpeg.tobytes()))])
    assert reading["doc_type"]["predicted"] == applicant.TOR
    assert reading["fields"]["student_number"]["value"] == "OLD-2020-0042"


def test_decode_refuses_garbage():
    with pytest.raises(ValueError):
        worker.decode(b"not an image")


def test_cli_needs_a_station_key(monkeypatch):
    monkeypatch.delenv("ADGVS_STATION_KEY", raising=False)
    with pytest.raises(SystemExit):
        worker.main(["--server", "http://api.test"])
