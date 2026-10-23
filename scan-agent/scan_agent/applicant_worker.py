"""Applicant intake OCR worker (docs/applicant-intake.md): python -m scan_agent.applicant_worker

Claims an online submission from the API with the station key, downloads its pages with the
lease token, reads them (Tesseract, locally) and sends the reading back. It extracts only: the
backend decides and creates the intake document. A job it cannot read is reported as an error
(the backend retries it, then marks it failed); the error text never carries document text.

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

from scan_agent.processing.applicant import build_reading

log = logging.getLogger(__name__)
JOBS = "/api/station/applicant-jobs"
IDLE_SECONDS = 5


def _default_ocr(image: np.ndarray) -> str:
    from scan_agent.processing.ocr import ocr_page_text  # Tesseract only when really reading
    return ocr_page_text(image)


def decode(content: bytes) -> np.ndarray:
    image = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError("A page image could not be decoded")
    return image


def _error_text(error: Exception) -> str:
    # The exception type and our own messages only: no OCR output goes into the error.
    return f"{type(error).__name__}: {error}"[:300]


def run_once(http: httpx.Client, ocr: Callable[[np.ndarray], str] = _default_ocr) -> bool:
    """One job. False when there was nothing to do."""
    res = http.post(f"{JOBS}/claim")
    res.raise_for_status()
    job = res.json().get("job")
    if not job:
        return False
    sub_id, token = job["submission_id"], job["lease_token"]
    try:
        texts = []
        for number in range(1, job["pages"] + 1):
            page = http.get(f"{JOBS}/{sub_id}/pages/{number}", headers={"X-Lease-Token": token})
            page.raise_for_status()
            texts.append(ocr(decode(page.content)))
        body = {"lease_token": token, "reading": build_reading(texts)}
    except (httpx.HTTPError, ValueError, RuntimeError) as error:
        log.warning("Submission %s could not be read: %s", sub_id, type(error).__name__)
        body = {"lease_token": token, "error": _error_text(error)}
    done = http.post(f"{JOBS}/{sub_id}/complete", json=body)
    if done.status_code == 409:  # lease expired and someone else took the job: drop it
        log.warning("Submission %s: lease lost, result discarded", sub_id)
        return True
    done.raise_for_status()
    log.info("Submission %s: %s", sub_id, done.json().get("status"))
    return True


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--server", default=os.environ.get("ADGVS_SERVER",
                                                           "http://127.0.0.1:8000"))
    parser.add_argument("--key", default=os.environ.get("ADGVS_STATION_KEY", ""))
    parser.add_argument("--once", action="store_true", help="one job, then exit")
    args = parser.parse_args(argv)
    if not args.key:
        parser.error("set ADGVS_STATION_KEY or pass --key")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with httpx.Client(base_url=args.server.rstrip("/"), headers={"X-Station-Key": args.key},
                      timeout=60, follow_redirects=False) as http:
        while True:
            try:
                busy = run_once(http)
            except httpx.HTTPError as error:  # API asleep or unreachable: jobs stay queued
                log.warning("API unavailable (%s); retrying", type(error).__name__)
                busy = False
            if args.once:
                return
            if not busy:
                time.sleep(IDLE_SECONDS)


if __name__ == "__main__":
    main()
