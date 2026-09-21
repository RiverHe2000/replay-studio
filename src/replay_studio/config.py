"""Explicit environment configuration; no credentials are written to logs."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    data_dir: Path
    database_url: str = ""
    lease_seconds: int = 60
    max_attempts: int = 3
    max_upload_bytes: int = 512 * 1024 * 1024
    project_quota_bytes: int = 2 * 1024 * 1024 * 1024
    chunk_size: int = 4 * 1024 * 1024
    session_hours: int = 24
    registration_open: bool = False
    secure_cookies: bool = False
    storage_backend: str = "local"
    s3_endpoint: str | None = None
    s3_bucket: str = "replay-studio"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_region: str = "us-east-1"
    allow_origins: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        root = self.data_dir.resolve()
        object.__setattr__(self, "data_dir", root)
        if not self.database_url:
            object.__setattr__(self, "database_url", "sqlite:///" + (root / "replay.sqlite3").as_posix())
        if self.lease_seconds < 2 or self.max_attempts < 1:
            raise ValueError("lease_seconds must be >=2 and max_attempts >=1")
        if self.storage_backend not in {"local", "s3"}:
            raise ValueError("REPLAY_STORAGE must be local or s3")

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            data_dir=Path(os.getenv("REPLAY_DATA_DIR", "data")),
            database_url=os.getenv("REPLAY_DATABASE_URL", ""),
            lease_seconds=int(os.getenv("REPLAY_LEASE_SECONDS", "60")),
            max_attempts=int(os.getenv("REPLAY_MAX_ATTEMPTS", "3")),
            max_upload_bytes=int(os.getenv("REPLAY_MAX_UPLOAD_BYTES", str(512 * 1024 * 1024))),
            project_quota_bytes=int(os.getenv("REPLAY_PROJECT_QUOTA_BYTES", str(2 * 1024**3))),
            registration_open=os.getenv("REPLAY_REGISTRATION_OPEN", "false").lower() == "true",
            secure_cookies=os.getenv("REPLAY_SECURE_COOKIES", "false").lower() == "true",
            storage_backend=os.getenv("REPLAY_STORAGE", "local"),
            s3_endpoint=os.getenv("REPLAY_S3_ENDPOINT"),
            s3_bucket=os.getenv("REPLAY_S3_BUCKET", "replay-studio"),
            s3_access_key=os.getenv("REPLAY_S3_ACCESS_KEY"),
            s3_secret_key=os.getenv("REPLAY_S3_SECRET_KEY"),
            s3_region=os.getenv("REPLAY_S3_REGION", "us-east-1"),
            allow_origins=tuple(filter(None, os.getenv("REPLAY_ALLOWED_ORIGINS", "").split(","))),
        )
