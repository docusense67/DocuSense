"""Shrink scan page images before they are stored (Supabase Free has 1 GB for all files).

The station uploads lossless 300 dpi pages (4.0-4.5 MB each for a noisy simulated scan).
Stored pages are only looked at by the validation operator on the review screen, which crops
field zones in proportion to the image width, so a narrower grayscale JPEG keeps every crop
in place (measured: ~282 KiB per page at 1654 px, quality 70; text and grades stay sharp).
Nothing is whitened or thresholded: faint erasures and correction marks must stay visible
to the reviewer. The decision itself is made earlier, from the station's extracted fields;
nothing re-reads these files.
"""

import io
import warnings

from PIL import Image

# A4 at 600 dpi is ~35 Mpx; anything far beyond that is not a page (decompression bomb).
MAX_PIXELS = 60_000_000

# End-of-image markers. Checked explicitly because WeasyPrint sets Pillow's process-wide
# ImageFile.LOAD_TRUNCATED_IMAGES = True on import, after which Pillow silently pads a
# truncated upload instead of failing.
PNG_IEND = b"IEND\xaeB`\x82"
JPEG_EOI = b"\xff\xd9"


class InvalidScanImage(ValueError):
    pass


def _is_complete(content: bytes, image_format: str) -> bool:
    tail = content[-64:]
    if image_format == "PNG":
        return PNG_IEND in tail
    return JPEG_EOI in tail  # JPEG; trailing padding after EOI is allowed


def compress_page(content: bytes, *, max_width: int, quality: int) -> tuple[bytes, str]:
    """Return (bytes, extension): grayscale JPEG at most max_width wide, aspect kept.

    If the upload is already smaller than that (e.g. a bilevel PNG) and no wider, it is kept
    unchanged."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as source:
                if source.format not in {"PNG", "JPEG"}:
                    raise InvalidScanImage("Pages must be PNG or JPEG")
                if not _is_complete(content, source.format):
                    raise InvalidScanImage("Page image could not be read")
                width, height = source.size
                if width * height > MAX_PIXELS:
                    raise InvalidScanImage("Page image is too large")
                original_format = source.format
                target = (max_width, max(1, round(height * max_width / width)))
                if width > max_width and source.format == "JPEG":
                    source.draft("L", target)  # decode at a reduced scale: fast, low memory
                image = source.convert("L")
    except InvalidScanImage:
        raise
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError,
            Image.DecompressionBombWarning):
        raise InvalidScanImage("Page image could not be read") from None

    if image.width > max_width:
        image = image.resize(target, Image.Resampling.LANCZOS, reducing_gap=3.0)
    out = io.BytesIO()
    image.save(out, "JPEG", quality=quality, optimize=True)
    compressed = out.getvalue()
    if width <= max_width and len(content) <= len(compressed):
        return content, "png" if original_format == "PNG" else "jpg"
    return compressed, "jpg"


def render_pdf_pages(content: bytes, *, max_pages: int, max_width: int,
                     quality: int) -> list[tuple[bytes, str]]:
    """Split an uploaded PDF (applicant intake) into grayscale JPEG pages, max_width wide.

    Rendered straight at the target width, so a page never exists at full resolution in
    memory. Password-protected, empty or oversized PDFs are refused."""
    import pypdfium2 as pdfium  # only needed for applicant uploads

    try:
        pdf = pdfium.PdfDocument(content)
    except pdfium.PdfiumError:
        raise InvalidScanImage("The PDF could not be read (damaged or password-protected)") \
            from None
    try:
        if len(pdf) == 0:
            raise InvalidScanImage("The PDF has no pages")
        if len(pdf) > max_pages:
            raise InvalidScanImage(f"The PDF has more than {max_pages} pages")
        pages = []
        for index in range(len(pdf)):
            page = pdf[index]
            try:
                width, height = page.get_size()  # points
                if width <= 0 or height <= 0:
                    raise InvalidScanImage("A PDF page could not be read")
                scale = max_width / width
                if (width * scale) * (height * scale) > MAX_PIXELS:
                    raise InvalidScanImage("A PDF page is too large")
                image = page.render(scale=scale, grayscale=True).to_pil().convert("L")
            finally:
                page.close()
            out = io.BytesIO()
            image.save(out, "JPEG", quality=quality, optimize=True)
            pages.append((out.getvalue(), "jpg"))
        return pages
    except pdfium.PdfiumError:
        raise InvalidScanImage("The PDF could not be read") from None
    finally:
        pdf.close()
