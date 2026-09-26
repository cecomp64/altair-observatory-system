"""Where blobs live (SPEC §7): file-system locations (the NAS, rig shares,
the local cache) and S3.

Every location stores a blob at ``<root>/<logical path>`` (S3: ``<prefix>
<logical path>``), so each one stays browsable by hand. Writes never
overwrite: an existing file or object with the same SHA-256 counts as
already stored, and a different one raises :class:`IntegrityMismatch`.
"""
from __future__ import annotations

import base64
import os
import shutil
import stat
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from altair.config import Location
from altair.hashing import CHUNK, copy_hashing, sha256_file

COLD_CLASSES = {"GLACIER", "DEEP_ARCHIVE"}   # need a restore before reading (GLACIER_IR is instant)


class StorageError(Exception):
    pass


class IntegrityMismatch(StorageError):
    """A copy's content doesn't match the SHA-256 the catalog expects."""


class LocationUnavailable(StorageError):
    pass


class RestoreRequired(StorageError):
    pass


def make_read_only(path: Path) -> None:
    path.chmod(stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)


def remove_file(path: Path) -> None:
    """Delete a file Altair made read-only (Windows refuses otherwise)."""
    try:
        path.chmod(stat.S_IWRITE | stat.S_IREAD)
    except FileNotFoundError:
        return
    path.unlink(missing_ok=True)


# ── file systems ─────────────────────────────────────────────────────────
class FsLocation:
    """A folder tree: the NAS (``nas``), a rig's NINA share (``rig:<name>``),
    the processing PC's cache, or a read-only ``external:`` folder. Paths may
    be UNC paths (``//nas/astro``) on Windows."""

    kind = "fs"

    def __init__(self, name: str, root: str | Path, *, read_only: bool = False, write_verify: str = "readback"):
        self.name = name
        self.root = Path(root)
        self.read_only = read_only
        self.write_verify = write_verify

    def path(self, logical_path: str) -> Path:
        return self.root.joinpath(*logical_path.split("/"))

    def uri(self, logical_path: str) -> str:
        return str(self.path(logical_path))

    def reachable(self) -> bool:
        try:
            return self.root.is_dir() and os.access(self.root, os.R_OK)
        except OSError:
            return False

    def free_percent(self) -> float | None:
        try:
            usage = shutil.disk_usage(self.root)
        except OSError:
            return None
        return 100.0 * usage.free / usage.total if usage.total else None

    def free_bytes(self) -> int | None:
        try:
            return shutil.disk_usage(self.root).free
        except OSError:
            return None

    def write_verified(self, source: str | Path, logical_path: str, sha256: str) -> str:
        """The safe write (SPEC §7.3 step 6, §7.5): ``<path>.partial``, flush,
        read back and re-hash, rename, mark read-only. Returns the URI."""
        if self.read_only:
            raise StorageError(f"{self.name} is read-only")
        dest = self.path(logical_path)
        if dest.exists():
            existing = sha256_file(dest)
            if existing == sha256:
                return str(dest)
            raise IntegrityMismatch(f"{dest} already exists with SHA-256 {existing[:12]}…, expected {sha256[:12]}…; not overwritten")
        dest.parent.mkdir(parents=True, exist_ok=True)
        partial = dest.with_name(dest.name + ".partial")
        remove_file(partial)
        with open(source, "rb") as src, open(partial, "wb") as out:
            written, _ = copy_hashing(src, out)
        if written != sha256:
            remove_file(partial)
            raise IntegrityMismatch(f"{source} changed while being copied to {self.name} ({written[:12]}… vs {sha256[:12]}…)")
        if self.write_verify == "readback" and sha256_file(partial) != sha256:
            remove_file(partial)
            raise IntegrityMismatch(f"read-back of {partial} doesn't match; the write to {self.name} was not kept")
        os.replace(partial, dest)
        make_read_only(dest)
        return str(dest)

    def sha256(self, uri: str) -> str:
        return sha256_file(uri)

    def exists(self, uri: str) -> bool:
        return Path(uri).exists()

    def delete(self, uri: str) -> None:
        if self.read_only:
            raise StorageError(f"{self.name} is read-only")
        remove_file(Path(uri))

    def open(self, uri: str):
        return open(uri, "rb")


# ── S3 ───────────────────────────────────────────────────────────────────
@dataclass
class ObjectInfo:
    key: str
    size: int
    sha256: str | None           # x-amz-meta-altair-sha256
    checksum_sha256: str | None  # S3's own checksum (base64; composite for multipart)
    storage_class: str
    version_id: str | None
    restore: str | None          # None / "ongoing" / "done"
    retain_until: datetime | None

    @property
    def cold(self) -> bool:
        return self.storage_class in COLD_CLASSES and self.restore != "done"


class S3Location:
    """An S3 bucket prefix (SPEC §7.5). Altair uploads with SHA-256 checksums
    and conditional writes (never overwriting), tags each object with its data
    class for the lifecycle rules, and sets Object Lock retention on kept
    classes when configured."""

    kind = "s3"

    def __init__(self, cfg: Location, client: Any = None):
        self.cfg = cfg
        self.name = cfg.name
        self.bucket = cfg.bucket
        self.prefix = cfg.prefix if not cfg.prefix or cfg.prefix.endswith("/") else cfg.prefix + "/"
        self._client = client

    @property
    def client(self):
        if self._client is None:
            import boto3

            creds = self.cfg.credentials
            session = boto3.session.Session(profile_name=creds.profile) if creds.profile else boto3.session.Session(
                aws_access_key_id=creds.access_key_id, aws_secret_access_key=creds.secret_access_key)
            self._client = session.client("s3", region_name=self.cfg.region, endpoint_url=self.cfg.endpoint_url)
        return self._client

    def key(self, logical_path: str) -> str:
        return self.prefix + logical_path

    def uri(self, logical_path: str) -> str:
        return f"s3://{self.bucket}/{self.key(logical_path)}"

    def logical_path(self, key: str) -> str:
        return key[len(self.prefix):] if key.startswith(self.prefix) else key

    def reachable(self) -> bool:
        try:
            self.client.head_bucket(Bucket=self.bucket)
            return True
        except Exception:  # noqa: BLE001 - any failure means "not now"
            return False

    # ── writes ───────────────────────────────────────────────────────────
    def upload(self, source: str | Path, logical_path: str, sha256: str, data_class: str, *,
               bytes_per_s: float | None = None) -> ObjectInfo:
        """Upload once; never overwrite. Returns the verified object.
        ``bytes_per_s`` throttles the upload (``upload_bandwidth_limit_mbps``)."""
        from botocore.exceptions import ClientError

        key = self.key(logical_path)
        size = Path(source).stat().st_size
        extra: dict[str, Any] = {"Metadata": {"altair-sha256": sha256}, "StorageClass": self.cfg.class_storage(data_class),
                                 "Tagging": f"altair-class={data_class}"}
        lock = self.cfg.object_lock
        if lock and data_class in lock.classes:
            extra["ObjectLockMode"] = lock.mode
            extra["ObjectLockRetainUntilDate"] = datetime.now(timezone.utc) + timedelta(days=365 * lock.retain_years)
        chunk = self.cfg.multipart_chunk_mb << 20
        try:
            if size <= chunk:
                with open(source, "rb") as handle:
                    body = _Throttled(handle, bytes_per_s) if bytes_per_s else handle
                    self.client.put_object(Bucket=self.bucket, Key=key, Body=body, IfNoneMatch="*", ChecksumAlgorithm="SHA256",
                                           ChecksumSHA256=base64.b64encode(bytes.fromhex(sha256)).decode(), **extra)
            else:
                self._multipart(source, key, chunk, extra, bytes_per_s)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") not in ("PreconditionFailed", "412"):
                raise
        info = self.head(logical_path)
        if info is None or info.sha256 != sha256 or info.size != size:
            found = info.sha256[:12] if info and info.sha256 else "none"
            raise IntegrityMismatch(f"s3://{self.bucket}/{key} holds SHA-256 {found}…, expected {sha256[:12]}…; not overwritten")
        return info

    def _multipart(self, source: str | Path, key: str, chunk: int, extra: dict, bytes_per_s: float | None = None) -> None:
        """Multipart with per-part SHA-256 checksums, resuming an unfinished
        upload of the same key (a lifecycle rule aborts orphans after 7 days)."""
        upload_id = None
        done: dict[int, dict] = {}
        for upload in self.client.list_multipart_uploads(Bucket=self.bucket, Prefix=key).get("Uploads", []):
            if upload["Key"] == key:
                upload_id = upload["UploadId"]
                for part in self.client.list_parts(Bucket=self.bucket, Key=key, UploadId=upload_id).get("Parts", []):
                    done[part["PartNumber"]] = {"PartNumber": part["PartNumber"], "ETag": part["ETag"], "ChecksumSHA256": part.get("ChecksumSHA256")}
                break
        if upload_id is None:
            upload_id = self.client.create_multipart_upload(Bucket=self.bucket, Key=key, ChecksumAlgorithm="SHA256", **extra)["UploadId"]
        with open(source, "rb") as handle:
            number = 0
            while block := handle.read(chunk):
                number += 1
                if number in done:
                    continue
                started = time.monotonic()
                part = self.client.upload_part(Bucket=self.bucket, Key=key, UploadId=upload_id, PartNumber=number, Body=block,
                                               ChecksumAlgorithm="SHA256")
                done[number] = {"PartNumber": number, "ETag": part["ETag"], "ChecksumSHA256": part.get("ChecksumSHA256")}
                if bytes_per_s:
                    time.sleep(max(0.0, len(block) / bytes_per_s - (time.monotonic() - started)))
        parts = [{k: v for k, v in done[n].items() if v is not None} for n in sorted(done)]
        self.client.complete_multipart_upload(Bucket=self.bucket, Key=key, UploadId=upload_id, MultipartUpload={"Parts": parts},
                                              IfNoneMatch="*")

    def rewrite(self, source: str | Path, logical_path: str, sha256: str, data_class: str) -> ObjectInfo:
        """Scrub repair only (SPEC §7.8): re-upload a corrupt object under the
        same key from a verified copy. Versioning keeps the bad version for
        inspection. The content written always matches the catalog's SHA-256."""
        from altair.hashing import sha256_file

        if sha256_file(source) != sha256:
            raise IntegrityMismatch(f"{source} is not a good copy of {logical_path}")
        with open(source, "rb") as body:
            self.client.put_object(Bucket=self.bucket, Key=self.key(logical_path), Body=body, ChecksumAlgorithm="SHA256",
                                   ChecksumSHA256=base64.b64encode(bytes.fromhex(sha256)).decode(), Metadata={"altair-sha256": sha256},
                                   StorageClass=self.cfg.class_storage(data_class), Tagging=f"altair-class={data_class}")
        info = self.head(logical_path)
        if info is None or info.sha256 != sha256:
            raise IntegrityMismatch(f"rewrite of {logical_path} didn't verify")
        return info

    # ── reads ────────────────────────────────────────────────────────────
    def head(self, logical_path: str) -> ObjectInfo | None:
        from botocore.exceptions import ClientError

        key = self.key(logical_path)
        try:
            h = self.client.head_object(Bucket=self.bucket, Key=key, ChecksumMode="ENABLED")
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise
        restore = h.get("Restore")
        state = None if not restore else ("ongoing" if 'ongoing-request="true"' in restore else "done")
        return ObjectInfo(key=key, size=h["ContentLength"], sha256=(h.get("Metadata") or {}).get("altair-sha256"),
                          checksum_sha256=h.get("ChecksumSHA256"), storage_class=h.get("StorageClass") or "STANDARD",
                          version_id=h.get("VersionId"), restore=state, retain_until=h.get("ObjectLockRetainUntilDate"))

    def verify_fresh(self, logical_path: str, sha256: str, size: int | None = None) -> ObjectInfo | None:
        """A fresh HeadObject (no data downloaded) confirms the object exists,
        its size, and its stored SHA-256 (SPEC §7.6 step 2)."""
        info = self.head(logical_path)
        if info is None or info.sha256 != sha256 or (size is not None and info.size != size):
            return None
        return info

    def download(self, logical_path: str, dest: str | Path, sha256: str) -> Path:
        """Resumable (HTTP range) download with a streaming SHA-256 check."""
        from botocore.exceptions import ClientError

        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        partial = dest.with_name(dest.name + ".partial")
        offset = partial.stat().st_size if partial.exists() else 0
        kwargs: dict[str, Any] = {"Bucket": self.bucket, "Key": self.key(logical_path)}
        if offset:
            kwargs["Range"] = f"bytes={offset}-"
        try:
            body = self.client.get_object(**kwargs)["Body"]
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code == "InvalidObjectState":
                raise RestoreRequired(f"{logical_path} is in cold storage and must be restored first") from exc
            if code == "InvalidRange":
                offset = 0
                partial.unlink(missing_ok=True)
                body = self.client.get_object(Bucket=self.bucket, Key=self.key(logical_path))["Body"]
            else:
                raise
        with open(partial, "ab" if offset else "wb") as out:
            for block in body.iter_chunks(CHUNK):
                out.write(block)
        got = sha256_file(partial)
        if got != sha256:
            partial.unlink(missing_ok=True)
            raise IntegrityMismatch(f"download of {logical_path} has SHA-256 {got[:12]}…, expected {sha256[:12]}…")
        os.replace(partial, dest)
        make_read_only(dest)
        return dest

    def request_restore(self, logical_path: str) -> None:
        from botocore.exceptions import ClientError

        try:
            self.client.restore_object(Bucket=self.bucket, Key=self.key(logical_path), RestoreRequest={
                "Days": self.cfg.restore.days, "GlacierJobParameters": {"Tier": self.cfg.restore.tier}})
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "RestoreAlreadyInProgress":
                raise

    def delete(self, logical_path: str, version_id: str | None = None) -> None:
        """Only superseded multi-night versions and old catalog backups; the
        daemon's IAM policy allows nothing else (SPEC §7.5)."""
        kwargs = {"Bucket": self.bucket, "Key": self.key(logical_path)}
        if version_id:
            kwargs["VersionId"] = version_id
        self.client.delete_object(**kwargs)

    def list(self, prefix: str = "") -> Iterator[dict]:
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self.key(prefix)):
            yield from page.get("Contents", [])


class _Throttled:
    """A file wrapper that paces reads to ``bytes_per_s``."""

    def __init__(self, handle, bytes_per_s: float):
        self.handle, self.rate, self.start, self.done = handle, bytes_per_s, time.monotonic(), 0

    def read(self, n: int = -1) -> bytes:
        block = self.handle.read(n)
        self.done += len(block)
        ahead = self.done / self.rate - (time.monotonic() - self.start)
        if ahead > 0:
            time.sleep(ahead)
        return block

    def __getattr__(self, name: str):
        return getattr(self.handle, name)


# ── building locations from the config ───────────────────────────────────
def nas_location(config) -> FsLocation | None:
    cfg = config.storage.nas
    if not cfg or not cfg.root:
        return None
    return FsLocation("nas", cfg.root, write_verify=cfg.write_verify)


def s3_location(config, client: Any = None) -> S3Location | None:
    cfg = config.storage.s3
    return S3Location(cfg, client) if cfg and cfg.bucket else None


def rig_location(config, rig: str) -> FsLocation | None:
    root = config.rigs[rig].raw_root
    return FsLocation(f"rig:{rig}", root) if root else None
