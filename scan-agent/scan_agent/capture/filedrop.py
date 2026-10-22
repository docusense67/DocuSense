"""File-drop mode (Section 24): process images from a folder instead of a scanner."""

from collections.abc import Iterator
from pathlib import Path

import cv2

from scan_agent.capture.base import CapturedPage, PageSource, ScannerState

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}


class FolderSource(PageSource):
    def __init__(self, folder: str | Path, files: list[Path] | None = None):
        self.folder = Path(folder)
        self.files = files  # a fixed stack (watch mode); None = whatever is in the folder
        self.consumed: list[Path] = []  # files fully read in this run, in order

    def status(self) -> ScannerState:
        return ScannerState.READY if self.folder.is_dir() else ScannerState.OFFLINE

    def pages(self, start_sequence: int = 1) -> Iterator[CapturedPage]:
        # Sorted by name so "page-001.png, page-002.png" reproduces stack order.
        files = sorted(self.files if self.files is not None else (
            p for p in self.folder.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES | {".pdf"}))
        sequence = 0
        for path in files:
            for image in _read(path):
                sequence += 1
                if sequence >= start_sequence:
                    yield CapturedPage(sequence=sequence, image=image, source=str(path))
            self.consumed.append(path)

    def delete_consumed(self) -> int:
        """Remove the files this run read, once every document was uploaded (Section 18:
        scan images are not kept on the station)."""
        for path in self.consumed:
            path.unlink(missing_ok=True)
        count, self.consumed = len(self.consumed), []
        return count


def _read(path: Path) -> Iterator:
    """Images as-is; PDFs (e.g. a scanner's PDF output) rasterized at 300 DPI per page."""
    if path.suffix.lower() == ".pdf":
        import numpy as np
        import pypdfium2

        for page in pypdfium2.PdfDocument(str(path)):
            yield np.array(page.render(scale=300 / 72).to_pil().convert("L"))
        return
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError(f"Unreadable image: {path}")
    yield image
