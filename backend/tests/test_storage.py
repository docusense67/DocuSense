"""File storage backends (app/storage.py). Supabase is exercised with httpx.MockTransport:
no network, but every request is checked the way Supabase Storage would see it."""

import json

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from app import storage as storage_module
from app.config import Settings, get_settings
from app.storage import (
    LocalStorage,
    StorageError,
    StoredFileMissing,
    SupabaseStorage,
    check_key,
    get_storage,
)

URL = "https://xbirhmmdctcqniwbbccy.supabase.co"
KEY = "sb_secret_test-only-not-a-real-key"


class FakeBucket:
    """A private Supabase bucket behind the Storage REST API, in memory."""

    def __init__(self, bucket: str = "adgvs-private", *, exists: bool = True,
                 public: bool = False, legacy_404: bool = False):
        self.bucket, self.exists, self.public, self.legacy_404 = bucket, exists, public, legacy_404
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.requests: list[httpx.Request] = []

    def _missing(self, what: str) -> httpx.Response:
        if self.legacy_404:  # how older Storage versions answer
            return httpx.Response(400, json={"statusCode": "404", "error": "not_found",
                                             "message": f"{what} not found"})
        return httpx.Response(404, json={"error": "not_found"})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers["apikey"] == KEY
        assert request.headers["authorization"] == f"Bearer {KEY}"
        path = request.url.path
        if path == f"/storage/v1/bucket/{self.bucket}" and request.method == "GET":
            if not self.exists:
                return self._missing("Bucket")
            return httpx.Response(200, json={"id": self.bucket, "public": self.public})
        if path == "/storage/v1/bucket" and request.method == "POST":
            body = json.loads(request.content)
            assert body["allowed_mime_types"] == ["application/pdf", "image/jpeg", "image/png"]
            self.exists, self.public = True, body["public"]
            return httpx.Response(200, json={"name": self.bucket})
        prefix = f"/storage/v1/object/{self.bucket}/"
        assert path.startswith(prefix), path
        key = path.removeprefix(prefix)
        if request.method == "POST":
            assert request.headers["x-upsert"] == "true"
            self.objects[key] = (request.content, request.headers["content-type"])
            return httpx.Response(200, json={"Key": f"{self.bucket}/{key}"})
        if request.method == "GET":
            if key not in self.objects:
                return self._missing("Object")
            return httpx.Response(200, content=self.objects[key][0])
        if request.method == "DELETE":
            if key not in self.objects:
                return self._missing("Object")
            del self.objects[key]
            return httpx.Response(200, json={"message": "Successfully deleted"})
        return httpx.Response(405)


def supabase(bucket: FakeBucket) -> SupabaseStorage:
    return SupabaseStorage(URL, KEY, bucket.bucket, transport=httpx.MockTransport(bucket))


# ---- keys -----------------------------------------------------------------------------------

@pytest.mark.parametrize("key", ["documents/TOR-2026-000001.pdf", "scans/3/17/page-1.jpg"])
def test_valid_keys(key):
    assert check_key(key) == key


@pytest.mark.parametrize("key", ["../etc/passwd", "documents/../../x", "/abs/path", "a//b",
                                 ".hidden", "documents/", "", "a\\b", "x?y", "a/./b"])
def test_unsafe_keys_rejected(key):
    with pytest.raises(ValueError):
        check_key(key)


# ---- local ----------------------------------------------------------------------------------

def test_local_roundtrip_and_missing(tmp_path):
    store = LocalStorage(tmp_path)
    store.put("documents/A.pdf", b"%PDF-1.7", "application/pdf")
    assert (tmp_path / "documents" / "A.pdf").read_bytes() == b"%PDF-1.7"
    assert store.get("documents/A.pdf") == b"%PDF-1.7"
    with pytest.raises(StoredFileMissing):
        store.get("documents/B.pdf")
    with pytest.raises(StoredFileMissing):  # a traversal key reads nothing
        store.get("../outside.pdf")
    with pytest.raises(ValueError):
        store.put("../outside.pdf", b"x", "application/pdf")
    assert not (tmp_path.parent / "outside.pdf").exists()


def test_local_delete(tmp_path):
    store = LocalStorage(tmp_path)
    store.put("documents/A.pdf", b"%PDF-1.7", "application/pdf")
    assert store.delete("documents/A.pdf") is True
    assert not (tmp_path / "documents" / "A.pdf").exists()
    assert store.delete("documents/A.pdf") is False
    with pytest.raises(ValueError):  # never outside the storage root
        store.delete("../outside.pdf")


# ---- supabase -------------------------------------------------------------------------------

def test_supabase_put_and_get():
    bucket = FakeBucket()
    store = supabase(bucket)
    store.put("scans/3/17/page-1.jpg", b"\xff\xd8\xffdata", "image/jpeg")
    upload = bucket.requests[-1]
    assert upload.method == "POST"
    assert str(upload.url) == f"{URL}/storage/v1/object/adgvs-private/scans/3/17/page-1.jpg"
    assert upload.headers["content-type"] == "image/jpeg"
    assert bucket.objects["scans/3/17/page-1.jpg"] == (b"\xff\xd8\xffdata", "image/jpeg")
    assert store.get("scans/3/17/page-1.jpg") == b"\xff\xd8\xffdata"


@pytest.mark.parametrize("legacy", [False, True])
def test_supabase_missing_object(legacy):
    store = supabase(FakeBucket(legacy_404=legacy))
    with pytest.raises(StoredFileMissing):
        store.get("documents/NOPE.pdf")


@pytest.mark.parametrize("legacy", [False, True])
def test_supabase_delete(legacy):
    bucket = FakeBucket(legacy_404=legacy)
    store = supabase(bucket)
    store.put("intake/2/9.jpg", b"\xff\xd8\xffdata", "image/jpeg")
    assert store.delete("intake/2/9.jpg") is True
    assert bucket.requests[-1].method == "DELETE"
    assert str(bucket.requests[-1].url) == f"{URL}/storage/v1/object/adgvs-private/intake/2/9.jpg"
    assert bucket.objects == {}
    assert store.delete("intake/2/9.jpg") is False


def test_supabase_delete_error_is_a_storage_error():
    store = SupabaseStorage(URL, KEY, "adgvs-private", transport=httpx.MockTransport(
        lambda request: httpx.Response(403, json={"error": "nope"})))
    with pytest.raises(StorageError):
        store.delete("documents/A.pdf")


def test_supabase_never_sends_an_unsafe_key():
    bucket = FakeBucket()
    store = supabase(bucket)
    with pytest.raises(ValueError):
        store.put("../other-bucket/x", b"x", "application/pdf")
    with pytest.raises(StoredFileMissing):
        store.get("../other-bucket/x")
    assert bucket.requests == []


@pytest.mark.parametrize("status", [401, 403, 413, 500, 503])
def test_supabase_errors_become_storage_errors(status):
    store = SupabaseStorage(URL, KEY, "adgvs-private", transport=httpx.MockTransport(
        lambda request: httpx.Response(status, json={"error": "nope"})))
    with pytest.raises(StorageError) as e:
        store.put("documents/A.pdf", b"x", "application/pdf")
    assert KEY not in str(e.value)
    with pytest.raises(StorageError):
        store.get("documents/A.pdf")


def test_supabase_network_failure_is_a_storage_error():
    def unreachable(request):
        raise httpx.ConnectError("connection refused", request=request)

    store = SupabaseStorage(URL, KEY, "adgvs-private", transport=httpx.MockTransport(unreachable))
    with pytest.raises(StorageError, match="unreachable"):
        store.get("documents/A.pdf")


def test_bucket_is_created_private():
    bucket = FakeBucket(exists=False, legacy_404=True)
    assert supabase(bucket).ensure_private_bucket() == "created"
    assert bucket.exists and bucket.public is False
    assert supabase(bucket).ensure_private_bucket() == "exists"


def test_public_bucket_is_refused():
    with pytest.raises(StorageError, match="public"):
        supabase(FakeBucket(public=True)).ensure_private_bucket()


# ---- selection and settings -----------------------------------------------------------------

def test_backend_selection(monkeypatch, tmp_path):
    settings = get_settings()
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    assert isinstance(get_storage(), LocalStorage)  # default: on-prem behaviour
    monkeypatch.setattr(settings, "storage_backend", "supabase")
    monkeypatch.setattr(settings, "supabase_url", URL)
    monkeypatch.setattr(settings, "supabase_service_key", SecretStr(KEY))
    storage_module._supabase.cache_clear()
    try:
        store = get_storage()
        assert isinstance(store, SupabaseStorage) and store.bucket == "adgvs-private"
        assert get_storage() is store  # one HTTP client, connections reused
    finally:
        storage_module._supabase.cache_clear()


def test_supabase_settings_must_be_complete():
    with pytest.raises(ValidationError):
        Settings(storage_backend="supabase", supabase_url="", supabase_service_key=KEY)
    with pytest.raises(ValidationError):
        Settings(storage_backend="supabase", supabase_url=URL, supabase_service_key="")
    with pytest.raises(ValidationError):
        Settings(storage_backend="supabase", supabase_url="http://plain.example",
                 supabase_service_key=KEY)
    with pytest.raises(ValidationError):
        Settings(storage_bucket="Bad Bucket/..")
    settings = Settings(storage_backend="supabase", supabase_url=URL, supabase_service_key=KEY)
    assert KEY not in repr(settings)  # SecretStr: never printed or logged
