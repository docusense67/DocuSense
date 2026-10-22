"""Document number, QR token and payload, snapshot fingerprint (Sections 5F, 8 Level 0)."""

import base64
import hashlib
import json
import secrets

import qrcode
import qrcode.image.svg
from sqlalchemy.orm import Session

from app.models import GeneratedDocument
from app.models.enums import DocumentType
from app.services.numbering import next_number

# The QR holds only this prefix plus a random token, never personal data (Section 18).
QR_PREFIX = "ADGVS1:"
SHORT_HASH_LENGTH = 12


def next_document_number(db: Session, doc_type: DocumentType, year: int) -> str:
    """TOR-2026-000123. The unique constraint on document_number guards concurrent issues."""
    return next_number(db, GeneratedDocument.document_number, f"{doc_type.value}-{year}-")


def new_qr_token() -> str:
    return secrets.token_urlsafe(24)


def qr_payload(token: str) -> str:
    return QR_PREFIX + token


def qr_data_uri(payload: str) -> str:
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,  # quiet zone the scanner needs
        image_factory=qrcode.image.svg.SvgPathImage,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    svg = qr.make_image().to_string()
    return "data:image/svg+xml;base64," + base64.b64encode(svg).decode()


def canonical_json(snapshot: dict) -> bytes:
    return json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def snapshot_hash(snapshot: dict) -> str:
    return hashlib.sha256(canonical_json(snapshot)).hexdigest()


def short_hash(digest: str) -> str:
    return digest[:SHORT_HASH_LENGTH]


def footer_text(document_number: str, digest: str) -> tuple[str, str]:
    """Parts of the system footer printed on every page around 'Page X of Y'."""
    return document_number, f"Ref {short_hash(digest)}"
