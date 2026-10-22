"""Level 0 identity: the QR holds only "ADGVS1:<random token>" (Section 8)."""

import cv2
import numpy as np

QR_PREFIX = "ADGVS1:"

# (payload, (left, top, width, height) in pixels)
Found = tuple[str, tuple[int, int, int, int]]


def locate_qr(page: np.ndarray) -> Found | None:
    """The first ADGVS QR on the page and where it is (for the Level 2 layout check)."""
    for payload, box in _pyzbar(page) + _opencv(page):
        if payload.startswith(QR_PREFIX):
            return payload, box
    return None


def decode_qr(page: np.ndarray) -> str | None:
    """Return the first ADGVS payload found on the page, or None."""
    found = locate_qr(page)
    return found[0] if found else None


def token_from_payload(payload: str | None) -> str | None:
    if payload and payload.startswith(QR_PREFIX):
        return payload[len(QR_PREFIX):]
    return None


def _pyzbar(page: np.ndarray) -> list[Found]:
    try:
        from pyzbar import pyzbar
    except (ImportError, OSError):  # zbar library missing: fall back to OpenCV
        return []
    return [(d.data.decode("utf-8", "replace"), tuple(d.rect)) for d in pyzbar.decode(page)
            if d.type == "QRCODE"]


def _opencv(page: np.ndarray) -> list[Found]:
    text, points, _ = cv2.QRCodeDetector().detectAndDecode(page)
    if not text or points is None:
        return []
    xs, ys = points.reshape(-1, 2)[:, 0], points.reshape(-1, 2)[:, 1]
    return [(text, (int(xs.min()), int(ys.min()), int(xs.max() - xs.min()),
                    int(ys.max() - ys.min())))]
