"""Capture contract shared by every page source (file-drop, TWAIN, SANE).

The rest of the agent (clean-up, QR, OCR, upload) only sees `CapturedPage` objects,
so the hardware driver and the processing pipeline can be built in parallel.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum

import numpy as np


class ScannerState(StrEnum):
    READY = "ready"
    SCANNING = "scanning"
    JAM = "jam"
    DOUBLE_FEED = "double_feed"
    OFFLINE = "offline"


class ScannerError(Exception):
    """Raised mid-batch on jam / double-feed. The session resumes from `next_sequence`.

    `detail` is an optional operator-facing explanation for the dashboard (e.g. "Cover
    open"); the message falls back to the generic re-feed instruction."""

    def __init__(self, state: ScannerState, next_sequence: int, detail: str | None = None):
        super().__init__(detail or f"Scanner {state}; re-feed from sheet {next_sequence}")
        self.state = state
        self.next_sequence = next_sequence
        self.detail = detail


@dataclass(frozen=True)
class CapturedPage:
    sequence: int  # 1-based position in the feed for this session
    image: np.ndarray  # grayscale, uint8
    dpi: int = 300
    source: str = ""  # file path or device name, for diagnostics


class PageSource(ABC):
    @abstractmethod
    def status(self) -> ScannerState: ...

    @abstractmethod
    def pages(self, start_sequence: int = 1) -> Iterator[CapturedPage]:
        """Yield pages in feed order. Raise ScannerError on jam / double-feed."""
