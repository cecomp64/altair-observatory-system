"""Uploads files to the telescope's S3 bucket."""

from __future__ import annotations

import logging
from pathlib import Path

import boto3

logger = logging.getLogger(__name__)


class S3Publisher:
    def __init__(self, bucket: str, prefix: str = "", region: str | None = None, client=None):
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.client = client or boto3.client("s3", region_name=region)

    def _key(self, *parts: str) -> str:
        segments = [self.prefix, *[p.strip("/") for p in parts if p]]
        return "/".join(segment for segment in segments if segment)

    def _url(self, key: str) -> str:
        return f"https://{self.bucket}.s3.amazonaws.com/{key}"

    def upload_file(self, local_path: Path, *key_parts: str) -> str:
        key = self._key(*key_parts, local_path.name)
        logger.info("Uploading %s -> s3://%s/%s", local_path, self.bucket, key)
        self.client.upload_file(str(local_path), self.bucket, key)
        return self._url(key)

    def upload_directory(self, local_dir: Path, *key_parts: str) -> list[tuple[Path, str]]:
        uploaded = []
        for path in sorted(local_dir.iterdir()):
            if path.is_file():
                url = self.upload_file(path, *key_parts)
                uploaded.append((path, url))
        return uploaded
