"""Page analysis, bulk splitting, and field extraction (Sections 9-10).

The agent extracts; the backend decides VALID / INVALID / NEEDS REVIEW. Only template
zones and page counts come back from the lookup, never a student's data.
"""

import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from difflib import SequenceMatcher

import numpy as np

from scan_agent.capture.base import CapturedPage
from scan_agent.config import FOOTER_ZONE, MM_TO_PX
from scan_agent.processing.cleanup import normalize_page
from scan_agent.processing.ocr import ocr_block, ocr_field, ocr_page_text
from scan_agent.processing.parsing import Footer, TableExtract, parse_footer, parse_table
from scan_agent.processing.qr import locate_qr, token_from_payload

TABLE_ZONES = {"grades_table", "grades_table_cont"}
MAX_PAGE_TEXT = 50_000
NON_TEXT_ZONES = {"qr_code"} | TABLE_ZONES
# Table OCR scale. The region is thresholded first (cleanup.binarize); at scan size the CI
# simulated set then reads 100% of grades, clean and rough. Enlarging the whole table did
# not read better and made the LSTM's 7 -> 1 confusion more likely ("2.75" -> "2.15"), at
# about 1.6x the time. ADGVS_TABLE_OCR_SCALE overrides it for tuning on real scans.
TABLE_OCR_SCALE = float(os.environ.get("ADGVS_TABLE_OCR_SCALE", "1.0"))

# lookup(qr_token, document_number) -> {"found", "document_number", "field_zones", ...}
Lookup = Callable[[str | None, str | None], dict]


@dataclass
class PageInfo:
    sequence: int
    image: np.ndarray
    skew: float
    qr_payload: str | None
    footer: Footer | None
    qr_box_mm: dict | None = None  # where the QR sits on the page (Level 2 layout check)
    source_image: np.ndarray | None = None  # full-text OCR keeps the camera's aspect ratio


@dataclass
class ExtractedDocument:
    pages: list[PageInfo]
    position: int  # 1-based position in the stack, so flagged paper can be found (Section 10)
    payload: dict = field(default_factory=dict)


def analyze_page(captured: CapturedPage) -> PageInfo:
    image, skew = normalize_page(captured.image)
    footer = parse_footer(ocr_field(image, FOOTER_ZONE, "footer"))
    found = locate_qr(image)
    box = None
    if found:
        left, top, width, height = found[1]
        box = {k: round(v / MM_TO_PX, 1) for k, v in
               (("x", left), ("y", top), ("w", width), ("h", height))}
    return PageInfo(sequence=captured.sequence, image=image, skew=skew,
                    qr_payload=found[0] if found else None, footer=footer, qr_box_mm=box,
                    source_image=captured.image)


def _same_number(a: str, b: str) -> bool:
    # Tolerate an OCR slip in a continuation page's footer (e.g. 0 read as O).
    return a == b or SequenceMatcher(None, a, b).ratio() >= 0.85


def split_documents(pages: Iterable[PageInfo]) -> list[list[PageInfo]]:
    """A new document starts at a page carrying a QR, at "Page 1 of N", or when the footer's
    document number changes. Unreadable continuation pages stay with the current document."""
    groups: list[list[PageInfo]] = []
    current_number: str | None = None
    for page in pages:
        footer = page.footer
        starts = (
            not groups
            or page.qr_payload is not None
            or (footer is not None and footer.page == 1)
            or (footer is not None and current_number is not None
                and not _same_number(footer.document_number, current_number))
        )
        if starts:
            groups.append([page])
            current_number = footer.document_number if footer else None
        else:
            groups[-1].append(page)
            if current_number is None and footer is not None:
                current_number = footer.document_number
    return groups


def _pages_for(zone: dict, count: int) -> range:
    return {"first": range(0, 1), "rest": range(1, count), "all": range(0, count)}[
        zone.get("pages", "first")]


def _row_box_mm(px: dict | None, zone: dict, page: int) -> dict | None:
    """Region pixels -> page millimetres, spanning the table width, for a row crop."""
    if px is None:
        return None
    top = zone["y"] + px["top"] / MM_TO_PX
    bottom = zone["y"] + px["bottom"] / MM_TO_PX
    return {"page": page, "x": zone["x"], "y": round(top - 1.0, 2), "w": zone["w"],
            "h": round(bottom - top + 2.0, 2)}


def page_transcription(image: np.ndarray) -> dict:
    try:
        text = ocr_page_text(image)
        return {'ocr_text': text[:MAX_PAGE_TEXT], 'ocr_text_truncated': len(text) > MAX_PAGE_TEXT}
    except RuntimeError:
        # Transcription is for display. Its timeout must not replace field-based findings.
        return {'ocr_text': None, 'ocr_text_error': 'Full-page OCR could not finish.'}


def extract_document(pages: list[PageInfo], position: int, lookup: Lookup) -> ExtractedDocument:
    first = pages[0]
    token = token_from_payload(first.qr_payload)
    footer_number = first.footer.document_number if first.footer else None
    identity = "qr" if token else ("ocr" if footer_number else "none")
    found = lookup(token, footer_number) if identity != "none" else {"found": False}

    fields: dict[str, dict] = {}
    table: TableExtract | None = None
    if found.get("found"):
        zones = found["field_zones"]["fields"]
        for name, zone in zones.items():
            if name in NON_TEXT_ZONES:
                continue
            line = ocr_field(first.image, zone, name)
            fields[name] = {"value": line.text, "confidence": line.confidence}

        if TABLE_ZONES & zones.keys():
            table = TableExtract()
            term = None
            for name in ("grades_table", "grades_table_cont"):
                if name not in zones:
                    continue
                for index in _pages_for(zones[name], len(pages)):
                    zone = zones[name]
                    part = parse_table(ocr_block(pages[index].image, zone, strip_rules=True,
                                                 scale=TABLE_OCR_SCALE),
                                       term)
                    for row in part.rows:
                        row.box = _row_box_mm(row.box, zone, index + 1)
                    if part.header_box and table.header_box is None:
                        table.header_box = _row_box_mm(part.header_box, zone, index + 1)
                    table.rows.extend(part.rows)
                    table.term_gwas.extend(part.term_gwas)
                    table.unparsed.extend(part.unparsed)
                    if part.rows:
                        term = part.rows[-1].term

    payload = {
        "position": position,
        "identity_source": identity,
        "qr_token": token,
        "document_number_read": footer_number,
        "lookup_document_number": found.get("document_number"),
        "pages": [
            {"sequence": p.sequence, "skew": round(p.skew, 2),
             **page_transcription(p.source_image if p.source_image is not None else p.image),
             "qr_present": p.qr_payload is not None, "qr_box": p.qr_box_mm,
             "footer": None if p.footer is None else {
                 "document_number": p.footer.document_number, "page": p.footer.page,
                 "pages": p.footer.pages, "ref": p.footer.ref,
                 "confidence": p.footer.confidence}}
            for p in pages
        ],
        "fields": fields,
        "table": table.as_dict() if table else None,
    }
    return ExtractedDocument(pages=pages, position=position, payload=payload)


def process_stack(captured: Iterable[CapturedPage], lookup: Lookup) -> list[ExtractedDocument]:
    analyzed = [analyze_page(page) for page in captured]
    return [extract_document(group, position, lookup)
            for position, group in enumerate(split_documents(analyzed), start=1)]
