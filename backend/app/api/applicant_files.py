"""Page files of the applicant intake: uploads and streaming (docs/applicant-intake.md).

Shared by the applicant's and staff's submission uploads and by reference samples. One upload
request stays under the Vercel proxy's body limit; a PDF is split into page images. Files go
through app/storage.py and are streamed by the API: storage URLs never reach the browser.
"""

from fastapi import HTTPException, Response, UploadFile
from fastapi import status as http
from starlette.concurrency import run_in_threadpool

from app.config import get_settings
from app.services import applicant_intake as rules
from app.services.scan_images import InvalidScanImage, compress_page, render_pdf_pages
from app.services.scanning import IMAGE_MEDIA_TYPES
from app.storage import StoredFileMissing, get_storage

# OCR needs more detail than the validation review screen: keep a higher JPEG quality.
PAGE_JPEG_QUALITY = 85


async def read_upload(files: list[UploadFile]) -> list[tuple[bytes, str]]:
    """(content, kind) of each file; together they must stay under the per-request limit."""
    limit = get_settings().applicant_upload_max_bytes
    out, total = [], 0
    for file in files:
        content = await file.read(limit + 1 - total)
        total += len(content)
        if total > limit:
            raise HTTPException(http.HTTP_413_CONTENT_TOO_LARGE,
                                f"Each upload must be under {limit // 1_000_000} MB: upload the "
                                "pages one at a time or scan at a lower resolution")
        try:
            out.append((content, rules.upload_kind(content)))
        except rules.InvalidUpload as e:
            raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None
    return out


async def to_pages(content: bytes, kind: str, room: int) -> list[tuple[bytes, str]]:
    """Page images (data, extension) of one upload; a PDF gives at most `room` pages."""
    settings = get_settings()
    try:  # CPU-bound: off the event loop
        if kind == "pdf":
            return await run_in_threadpool(
                render_pdf_pages, content, max_pages=room,
                max_width=settings.scan_page_max_width, quality=PAGE_JPEG_QUALITY)
        return [await run_in_threadpool(
            compress_page, content, max_width=settings.scan_page_max_width,
            quality=PAGE_JPEG_QUALITY)]
    except InvalidScanImage as e:
        raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None


def store(prefix: str, token: str, data: bytes, extension: str) -> str:
    key = f"{prefix}/{token}.{extension}"
    get_storage().put(key, data, IMAGE_MEDIA_TYPES[extension])
    return key


def stream(key: str | None) -> Response:
    if key is None:
        raise HTTPException(http.HTTP_404_NOT_FOUND, "Page not found")
    try:
        content = get_storage().get(key)
    except StoredFileMissing:
        raise HTTPException(http.HTTP_404_NOT_FOUND, "Page missing") from None
    media = IMAGE_MEDIA_TYPES.get(key.rsplit(".", 1)[-1], "image/jpeg")
    return Response(content, media_type=media, headers={"Cache-Control": "no-store"})
