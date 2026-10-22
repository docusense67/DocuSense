from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="ADGVS_", extra="ignore")

    database_url: str = "postgresql+psycopg://adgvs:adgvs@localhost:5432/adgvs"
    # Postgres connection pool. Defaults suit the on-prem stack; on Render Free (512 MB,
    # Supabase pooler) use a smaller pool, e.g. 3 + 2.
    db_pool_size: int = Field(5, ge=1, le=50)
    db_max_overflow: int = Field(10, ge=0, le=50)

    # Where generated PDFs and scan page images live. `local` = files under storage_dir
    # (on-prem volume). `supabase` = a private Supabase Storage bucket, read and written only
    # by this API with the service key; files still reach browsers only through our routes.
    storage_backend: Literal["local", "supabase"] = "local"
    storage_dir: str = "storage"
    supabase_url: str = ""  # https://<project-ref>.supabase.co
    supabase_service_key: SecretStr = SecretStr("")  # server only, never sent to a browser
    storage_bucket: str = Field("adgvs-private", pattern=r"^[a-z0-9][a-z0-9._-]{2,62}$")

    # Proxies in front of the API that append to X-Forwarded-For (Render behind Vercel: 2).
    # 0 trusts no forwarded header: the client address is the TCP peer as the server sees it.
    trusted_proxy_hops: int = Field(0, ge=0, le=10)

    # Bearer secret for GET /api/internal/cron/daily (Vercel Cron). Unset = route is off (404).
    cron_secret: SecretStr = SecretStr("")

    # Local Pi camera mechanism; disabled for cloud/SANE deployments.
    mechanism_url: str = ""
    mechanism_key: SecretStr = SecretStr("")
    mechanism_station_id: int | None = Field(None, ge=1)

    # Section 5A / 18 security baseline
    session_cookie_name: str = "adgvs_session"
    session_idle_minutes: int = 30
    session_touch_seconds: int = 60  # how often a busy session's last-activity time is saved
    cookie_secure: bool = True
    max_failed_logins: int = 5
    lockout_minutes: int = 15  # then the account unlocks; a registrar/admin reset unlocks now
    min_password_length: int = 10

    cors_origins: list[str] = ["http://localhost:5173"]
    # Interactive API docs at /docs. Off in production; the reference ships as openapi.json.
    api_docs: bool = True

    # Section 5C: configurable grading scale. INC and DRP are always accepted.
    grade_scale: list[str] = [
        "1.00", "1.25", "1.50", "1.75", "2.00", "2.25", "2.50", "2.75", "3.00", "5.00",
    ]
    passing_grade: str = "3.00"  # numeric grades <= this are Passed

    max_import_bytes: int = 2_000_000

    # Signs short-lived download links. MUST be set to a long random value in production.
    secret_key: str = "dev-only-insecure-secret-change-me"
    download_link_seconds: int = 300

    # Section 14 request lifecycle
    payment_required: bool = False  # registrar ticks "payment confirmed" before processing
    auto_release_on_approval: bool = True  # False = registrar releases manually (pickup)
    download_window_days: int = 30

    # Section 15 thresholds. Starting values; tune on real scans (Section 27).
    ocr_confidence_threshold: float = 90.0
    name_review_similarity: float = 0.90
    max_scan_image_bytes: int = 15_000_000
    # Scan pages are stored as grayscale JPEG, at most this wide (1654 px = A4 at 200 dpi):
    # measured 282 KiB/page on average (max 383 KiB) on simulated noisy 300 dpi scans whose
    # station PNGs were 4.0-4.5 MB, i.e. ~3,700 pages per GB. 1240 px (150 dpi) gives ~176
    # KiB. Only the review screen shows them (crops scale with the image width); validation
    # uses the station's extracted fields, not these files.
    scan_page_max_width: int = Field(1654, ge=600, le=5000)
    scan_page_jpeg_quality: int = Field(70, ge=30, le=95)

    # Printed on every document. [Assumption] Placeholder values until the school confirms.
    institution_name: str = "Sample State University"
    institution_address: str = "Sample City, Philippines"
    registrar_name: str = "MA. CLARA D. REYES"
    registrar_title: str = "University Registrar"

    @model_validator(mode="after")
    def _supabase_storage_complete(self) -> "Settings":
        if self.mechanism_url:
            from urllib.parse import urlsplit
            url = urlsplit(self.mechanism_url)
            if (url.scheme != "http" or url.hostname not in {"localhost", "127.0.0.1"}
                    or url.username or url.password or url.path not in {"", "/"}
                    or url.query or url.fragment):
                raise ValueError("ADGVS_MECHANISM_URL must be a loopback HTTP origin")
            if len(self.mechanism_key.get_secret_value()) < 32 or self.mechanism_station_id is None:
                raise ValueError("Set a random ADGVS_MECHANISM_KEY (32+ chars) and MECHANISM_STATION_ID")
        if self.storage_backend == "supabase":
            if not self.supabase_url.startswith("https://"):
                raise ValueError("ADGVS_SUPABASE_URL must be the https:// project URL")
            if not self.supabase_service_key.get_secret_value():
                raise ValueError("ADGVS_SUPABASE_SERVICE_KEY is required for Supabase storage")
        if 0 < len(self.cron_secret.get_secret_value()) < 16:
            raise ValueError("ADGVS_CRON_SECRET must be at least 16 characters (or unset)")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
