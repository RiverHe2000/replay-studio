"""Immutable object keys with a local adapter and S3-backed materialization."""

from __future__ import annotations

import os
import re
import shutil
import uuid
from pathlib import Path

from .config import Config


class Storage:
    def __init__(self, config: Config):
        self.config = config
        self.root = config.data_dir / ("objects" if config.storage_backend == "local" else "object_cache")
        self.root.mkdir(parents=True, exist_ok=True)
        self.s3 = None
        if config.storage_backend == "s3":
            import boto3

            self.s3 = boto3.client(
                "s3", endpoint_url=config.s3_endpoint, region_name=config.s3_region,
                aws_access_key_id=config.s3_access_key, aws_secret_access_key=config.s3_secret_key,
            )

    def _path(self, key: str) -> Path:
        if not key or not re.fullmatch(r"[a-zA-Z0-9_./-]+", key) or any(p in {"", ".", ".."} for p in key.split("/")):
            raise ValueError("Invalid object key")
        result = (self.root / key).resolve()
        if not result.is_relative_to(self.root.resolve()):
            raise ValueError("Object escapes storage root")
        return result

    def local_path(self, key: str) -> Path:
        target = self._path(key)
        if target.is_file():
            return target
        if self.s3 is None:
            raise FileNotFoundError(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        # boto3 adds another suffix to this name. Keep temporary paths short on
        # Windows instead of extending already deeply nested artifact keys.
        temporary = self._temporary()
        try:
            self.s3.download_file(self.config.s3_bucket, key, str(temporary))
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return target

    def _temporary(self) -> Path:
        staging = self._path(".staging")
        staging.mkdir(parents=True, exist_ok=True)
        return staging / (uuid.uuid4().hex + ".tmp")

    def put_file(self, key: str, path: Path) -> None:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        if self.s3 is not None:
            self.s3.upload_file(str(path), self.config.s3_bucket, key)
        if path.resolve() != target:
            temp = self._temporary()
            try:
                shutil.copyfile(path, temp)
                os.replace(temp, target)
            finally:
                temp.unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        path = self._path(key)
        if self.s3 is None:
            return path.is_file()
        from botocore.exceptions import ClientError

        try:
            self.s3.head_object(Bucket=self.config.s3_bucket, Key=key)
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise

    def delete_prefix(self, prefix: str) -> None:
        # A validated directory boundary prevents sibling-prefix deletion.
        target = self._path(prefix.rstrip("/"))
        if self.s3 is not None:
            paginator = self.s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.config.s3_bucket, Prefix=prefix.rstrip("/") + "/"):
                objects = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
                if objects:
                    result = self.s3.delete_objects(Bucket=self.config.s3_bucket, Delete={"Objects": objects})
                    if result.get("Errors"):
                        raise RuntimeError("Object storage rejected one or more deletions; collection is incomplete")
        if target.is_dir():
            shutil.rmtree(target)
