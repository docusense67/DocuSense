"""File storage for generated PDFs and scan page images.

Keys are relative POSIX paths ("documents/TOR-2026-000001.pdf", "scans/3/17/page-1.jpg"),
stored in the database as-is. Two backends, chosen with ADGVS_STORAGE_BACKEND:

  local     files under ADGVS_STORAGE_DIR (on-prem: the Docker volume). Default.
  supabase  a PRIVATE Supabase Storage bucket, via its REST API with the service key.

Either way the browser only ever gets files through our own authorised routes: no public
bucket, no signed Supabase URLs, and the service key never leaves the server.
"""

import re
from functools import lru_cache
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

import httpx

from app.config import get_settings

KEY_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*")


class StorageError(RuntimeError):
    """The storage backend failed (network, auth, quota). Not the caller's fault: 503."""


class StoredFileMissing(LookupError):
    """No file under that key."""


def check_key(key: str) -> str:
    # Segments start with a letter or digit: no "..", no absolute paths, no hidden files.
    if len(key) > 512 or not KEY_PATTERN.fullmatch(key):
        raise ValueError(f"Invalid storage key: {key!r}")
    return key


class Storage(Protocol):
    def put(self, key: str, data: bytes, content_type: str) -> None: ...

    def get(self, key: str) -> bytes: ...

    def delete(self, key: str) -> bool: ...  # False when there was no such file


class LocalStorage:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        root = self.root.resolve()
        path = (root / check_key(key)).resolve()
        if root not in path.parents:
            raise ValueError(f"Invalid storage key: {key!r}")
        return path

    def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get(self, key: str) -> bytes:
        try:
            path = self._path(key)
        except ValueError:
            raise StoredFileMissing(key) from None
        if not path.is_file():
            raise StoredFileMissing(key)
        return path.read_bytes()

    def delete(self, key: str) -> bool:
        """Only `manage purge-demo` deletes stored files (SAMPLE data, never real records)."""
        path = self._path(key)
        if not path.is_file():
            return False
        path.unlink()
        return True


class SupabaseStorage:
    """Supabase Storage REST API (https://supabase.com/docs/guides/storage)."""

    def __init__(self, url: str, service_key: str, bucket: str,
                 transport: httpx.BaseTransport | None = None):
        self.bucket = bucket
        self.client = httpx.Client(
            base_url=url.rstrip("/") + "/storage/v1",
            # New secret keys (sb_secret_...) and legacy service_role JWTs both go in both.
            headers={"apikey": service_key, "Authorization": f"Bearer {service_key}"},
            timeout=httpx.Timeout(30.0, connect=10.0),
            transport=transport,
            follow_redirects=False,
        )

    def _object(self, key: str) -> str:
        return f"/object/{quote(self.bucket)}/{quote(check_key(key))}"

    @staticmethod
    def _not_found(res: httpx.Response) -> bool:
        # Older Storage versions answer a missing object with 400 and a "404" body.
        if res.status_code == 404:
            return True
        if res.status_code == 400:
            try:
                body = res.json()
            except ValueError:
                return False
            return str(body.get("statusCode")) == "404" or body.get("error") == "not_found"
        return False

    def _send(self, method: str, url: str, **kwargs) -> httpx.Response:
        try:
            return self.client.request(method, url, **kwargs)
        except httpx.HTTPError as e:
            raise StorageError(f"Storage unreachable: {type(e).__name__}") from None

    def put(self, key: str, data: bytes, content_type: str) -> None:
        res = self._send("POST", self._object(key), content=data,
                         headers={"Content-Type": content_type, "x-upsert": "true",
                                  "Cache-Control": "no-store"})
        if res.status_code not in (200, 201):
            raise StorageError(f"Storage upload failed: HTTP {res.status_code}")

    def get(self, key: str) -> bytes:
        try:
            url = self._object(key)
        except ValueError:
            raise StoredFileMissing(key) from None
        res = self._send("GET", url)
        if self._not_found(res):
            raise StoredFileMissing(key)
        if res.status_code != 200:
            raise StorageError(f"Storage download failed: HTTP {res.status_code}")
        return res.content

    def delete(self, key: str) -> bool:
        """Only `manage purge-demo` deletes stored files (SAMPLE data, never real records)."""
        res = self._send("DELETE", self._object(key))
        if self._not_found(res):
            return False
        if res.status_code != 200:
            raise StorageError(f"Storage delete failed: HTTP {res.status_code}")
        return True

    def ensure_private_bucket(self) -> str:
        """Create the bucket if missing. Refuses to use a bucket that is public."""
        res = self._send("GET", f"/bucket/{quote(self.bucket)}")
        if res.status_code == 200:
            if res.json().get("public"):
                raise StorageError(f"Bucket {self.bucket!r} is public; make it private first")
            return "exists"
        if not self._not_found(res):
            raise StorageError(f"Bucket check failed: HTTP {res.status_code}")
        res = self._send("POST", "/bucket", json={
            "id": self.bucket, "name": self.bucket, "public": False,
            # Defence in depth: only what this app stores (PDFs, scan pages), within limits.
            "file_size_limit": 20 * 1024 * 1024,
            "allowed_mime_types": ["application/pdf", "image/jpeg", "image/png"],
        })
        if res.status_code not in (200, 201):
            raise StorageError(f"Bucket creation failed: HTTP {res.status_code}")
        return "created"


@lru_cache(maxsize=1)
def _supabase(url: str, service_key: str, bucket: str) -> SupabaseStorage:
    return SupabaseStorage(url, service_key, bucket)  # one client: connections are reused


def get_storage() -> Storage:
    settings = get_settings()
    if settings.storage_backend == "supabase":
        return _supabase(settings.supabase_url, settings.supabase_service_key.get_secret_value(),
                         settings.storage_bucket)
    return LocalStorage(settings.storage_dir)
