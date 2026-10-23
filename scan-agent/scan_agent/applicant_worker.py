"""Applicant intake OCR worker (docs/applicant-intake.md): python -m scan_agent.applicant_worker

Claims jobs from the API with the station key, downloads the pages with the lease token, reads
and measures them (Tesseract, locally) and sends the reading back. Three kinds of job:

- "first": an applicant's upload. Text and fields, the document type (classifier corpus
  extended with the school's approved training reference samples) and the page layout
  (text anchors, word height and gap, line gap) for the comparison with the school's reference.
- "reference": a reference sample staff uploaded, read the same way.
- "second": the second OCR pass of an upload staff accepted: independent preprocessing (Otsu
  threshold) and segmentation (Tesseract psm 6).

It extracts and measures only: the backend decides, compares with the reference and creates
the intake document. A job it cannot read is reported as an error (the backend retries it,
then marks it failed); the error text never carries document text.

In the cloud setup it talks to the Render API directly (page downloads are too large for the
Vercel proxy), like the scan station.
"""

import argparse
import logging
import os
import time
from collections.abc import Callable

import cv2
import httpx
import numpy as np

from scan_agent.processing.applicant import build_reading, layout_from_data, text_from_data

log = logging.getLogger(__name__)
JOBS = "/api/station/applicant-jobs"
REFERENCES = "/api/station/applicant-references"
IDLE_SECONDS = 5
KINDS = "first,second,reference"

Ocr = Callable[[np.ndarray], str]
Measure = Callable[[np.ndarray], dict]
SecondRead = Callable[[np.ndarray], tuple[str, dict]]


def _default_ocr(image: np.ndarray) -> str:
    from scan_agent.processing.ocr import ocr_page_text  # Tesseract only when really reading
    return ocr_page_text(image)


def _image_data(gray: np.ndarray, psm: int) -> dict:
    import pytesseract

    from scan_agent.processing import ocr  # noqa: F401  (sets pytesseract's tesseract_cmd)
    return pytesseract.image_to_data(gray, config=f"--psm {psm}",
                                     output_type=pytesseract.Output.DICT, timeout=45)


def _default_measure(image: np.ndarray) -> dict:
    """First pass layout: Tesseract's word boxes on the page as stored (psm 3)."""
    height, width = image.shape[:2]
    return layout_from_data(_image_data(image, 3), width, height)


def _default_second(image: np.ndarray) -> tuple[str, dict]:
    """Second pass: independent preprocessing (Otsu threshold) and segmentation (psm 6)."""
    _, binary = cv2.threshold(image, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    data = _image_data(binary, 6)
    height, width = binary.shape[:2]
    return text_from_data(data), layout_from_data(data, width, height)


def decode(content: bytes) -> np.ndarray:
    image = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError("A page image could not be decoded")
    return image


def _error_text(error: Exception) -> str:
    # The exception type and our own messages only: no OCR output goes into the error.
    return f"{type(error).__name__}: {error}"[:300]


def _pages(http: httpx.Client, base: str, count: int, token: str) -> list[np.ndarray]:
    images = []
    for number in range(1, count + 1):
        page = http.get(f"{base}/pages/{number}", headers={"X-Lease-Token": token})
        page.raise_for_status()
        images.append(decode(page.content))
    return images


def routes(job: dict) -> tuple[str, str]:
    """(where the pages are, where the result goes) for a job of any kind. A job without a
    kind is a first reading (an API from before reference samples and the second pass)."""
    kind = job.get("kind", "first")
    if kind == "reference":
        base = f"{REFERENCES}/{job['reference_id']}"
        return base, f"{base}/complete"
    base = f"{JOBS}/{job['submission_id']}"
    return base, f"{base}/second-pass" if kind == "second" else f"{base}/complete"


def read_job(http: httpx.Client, job: dict, ocr: Ocr, measure: Measure,
             second: SecondRead) -> dict:
    """The reading of one job. Raises on any failure."""
    refs, labels = job.get("classifier") or [], job.get("field_labels") or {}
    images = _pages(http, routes(job)[0], job["pages"], job["lease_token"])
    if job.get("kind") == "second":
        texts, layouts = zip(*(second(image) for image in images), strict=True)
        return build_reading(list(texts), list(layouts), refs, labels,
                             engine="tesseract local, pass 2 (otsu, psm 6)")
    texts = [ocr(image) for image in images]
    layouts = [measure(image) for image in images]
    return build_reading(texts, layouts, refs, labels, engine="tesseract local, pass 1 (psm 3)")


def run_once(http: httpx.Client, ocr: Ocr = _default_ocr, measure: Measure = _default_measure,
             second: SecondRead = _default_second, kinds: str = KINDS) -> bool:
    """One job. False when there was nothing to do."""
    res = http.post(f"{JOBS}/claim", params={"kinds": kinds})
    res.raise_for_status()
    job = res.json().get("job")
    if not job:
        return False
    name = f"{job.get('kind', 'first')} {job.get('submission_id') or job.get('reference_id')}"
    token = job["lease_token"]
    try:
        body = {"lease_token": token, "reading": read_job(http, job, ocr, measure, second)}
    except (httpx.HTTPError, ValueError, RuntimeError) as error:
        log.warning("Job %s could not be read: %s", name, type(error).__name__)
        body = {"lease_token": token, "error": _error_text(error)}
    done = http.post(routes(job)[1], json=body)
    if done.status_code == 409:  # lease expired and someone else took the job: drop it
        log.warning("Job %s: lease lost, result discarded", name)
        return True
    done.raise_for_status()
    log.info("Job %s: %s", name, done.json().get("status"))
    return True


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--server", default=os.environ.get("ADGVS_SERVER",
                                                           "http://127.0.0.1:8000"))
    parser.add_argument("--key", default=os.environ.get("ADGVS_STATION_KEY", ""))
    parser.add_argument("--kinds", default=os.environ.get("ADGVS_APPLICANT_JOB_KINDS", KINDS),
                        help="job kinds to run (default: %(default)s)")
    parser.add_argument("--once", action="store_true", help="one job, then exit")
    args = parser.parse_args(argv)
    if not args.key:
        parser.error("set ADGVS_STATION_KEY or pass --key")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with httpx.Client(base_url=args.server.rstrip("/"), headers={"X-Station-Key": args.key},
                      timeout=60, follow_redirects=False) as http:
        while True:
            try:
                busy = run_once(http, kinds=args.kinds)
            except httpx.HTTPError as error:  # API asleep or unreachable: jobs stay queued
                log.warning("API unavailable (%s); retrying", type(error).__name__)
                busy = False
            if args.once:
                return
            if not busy:
                time.sleep(IDLE_SECONDS)


if __name__ == "__main__":
    main()
