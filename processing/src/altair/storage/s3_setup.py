"""S3 bucket setup and checks (SPEC §7.5).

`altair storage s3 init` (run once with an admin profile) turns on
versioning, default encryption and Block Public Access, applies the
lifecycle rules (transitions only, never expiry of kept data; rules select
objects by their ``altair-class`` tag), and prints the least-privilege IAM
policy for the daemon: no delete permission anywhere except superseded
multi-night versions and old catalog backups.

`check` is what `altair doctor` runs. Versioning off, or a delete under
``raw/`` succeeding, opens ``S3_CONFIG_UNSAFE``, which stops all cleanup.
"""
from __future__ import annotations

import json
from typing import Any

from altair.catalog.db import Catalog
from altair.config import AltairConfig, LifecycleRule, Location
from altair.issues import raise_issue, resolve_issue

CANARY = "raw/_altair_doctor/delete-canary"


def iam_policy(cfg: Location) -> dict[str, Any]:
    bucket, prefix = cfg.bucket, cfg.prefix
    return {
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow", "Action": ["s3:ListBucket", "s3:GetBucketVersioning", "s3:GetLifecycleConfiguration",
                                           "s3:GetBucketObjectLockConfiguration", "s3:ListBucketMultipartUploads"],
             "Resource": f"arn:aws:s3:::{bucket}"},
            {"Effect": "Allow", "Action": ["s3:PutObject", "s3:GetObject", "s3:GetObjectAttributes", "s3:GetObjectTagging",
                                           "s3:PutObjectTagging", "s3:PutObjectRetention", "s3:RestoreObject",
                                           "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"],
             "Resource": f"arn:aws:s3:::{bucket}/{prefix}*"},
            {"Effect": "Allow", "Action": ["s3:DeleteObject", "s3:DeleteObjectVersion"],
             "Resource": [f"arn:aws:s3:::{bucket}/{prefix}projects/*/multinight/*", f"arn:aws:s3:::{bucket}/{prefix}catalog/*"]},
        ],
    }


def lifecycle_rules(cfg: Location) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    for data_class, rule in cfg.lifecycle.items():
        if isinstance(rule, LifecycleRule):
            rules.append({"ID": f"altair-{data_class}-to-{rule.to.lower()}", "Status": "Enabled",
                          "Filter": {"And": {"Prefix": cfg.prefix, "Tags": [{"Key": "altair-class", "Value": data_class}]}},
                          "Transitions": [{"Days": rule.after_days, "StorageClass": rule.to}]})
    abort = cfg.lifecycle.get("abort_incomplete_multipart_after_days")
    if isinstance(abort, int):
        rules.append({"ID": "altair-abort-incomplete-multipart", "Status": "Enabled", "Filter": {"Prefix": cfg.prefix},
                      "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": abort}})
    return rules


def init(client: Any, cfg: Location) -> dict[str, Any]:
    """Create/configure the bucket with an admin client. Returns the IAM policy."""
    from botocore.exceptions import ClientError

    bucket = cfg.bucket
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError:
        kwargs: dict[str, Any] = {"Bucket": bucket, "ObjectLockEnabledForBucket": cfg.object_lock is not None}
        if cfg.region and cfg.region != "us-east-1":
            kwargs["CreateBucketConfiguration"] = {"LocationConstraint": cfg.region}
        client.create_bucket(**kwargs)
    client.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
    client.put_bucket_encryption(Bucket=bucket, ServerSideEncryptionConfiguration={
        "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]})
    client.put_public_access_block(Bucket=bucket, PublicAccessBlockConfiguration={
        "BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": True, "RestrictPublicBuckets": True})
    rules = lifecycle_rules(cfg)
    if rules:
        client.put_bucket_lifecycle_configuration(Bucket=bucket, LifecycleConfiguration={"Rules": rules})
    return iam_policy(cfg)


def check(client: Any, cfg: Location) -> list[tuple[str, bool, str]]:
    """(label, passed, detail) rows for `altair doctor` / `storage s3 check`,
    using the daemon's own credentials."""
    from botocore.exceptions import ClientError

    bucket = cfg.bucket
    out: list[tuple[str, bool, str]] = []

    def call(label: str, fn, ok, detail=lambda r: ""):
        try:
            result = fn()
            out.append((label, bool(ok(result)), detail(result)))
        except ClientError as exc:
            out.append((label, False, exc.response.get("Error", {}).get("Code", str(exc))))

    call("bucket versioning enabled", lambda: client.get_bucket_versioning(Bucket=bucket), lambda r: r.get("Status") == "Enabled",
         lambda r: r.get("Status") or "off")
    call("default encryption", lambda: client.get_bucket_encryption(Bucket=bucket), lambda r: bool(r["ServerSideEncryptionConfiguration"]["Rules"]))
    call("public access blocked", lambda: client.get_public_access_block(Bucket=bucket),
         lambda r: all(r["PublicAccessBlockConfiguration"].values()))
    call("lifecycle rules", lambda: client.get_bucket_lifecycle_configuration(Bucket=bucket), lambda r: bool(r.get("Rules")),
         lambda r: f"{len(r.get('Rules', []))} rule(s)")
    if cfg.object_lock:
        call("object lock", lambda: client.get_object_lock_configuration(Bucket=bucket),
             lambda r: r["ObjectLockConfiguration"].get("ObjectLockEnabled") == "Enabled")
    # The daemon must not be able to delete under raw/ (enforced by IAM, not by Altair's code).
    key = f"{cfg.prefix}{CANARY}"
    try:
        client.put_object(Bucket=bucket, Key=key, Body=b"altair doctor delete canary", IfNoneMatch="*")
    except ClientError:
        pass
    try:
        client.delete_object(Bucket=bucket, Key=key)
        out.append(("delete denied under raw/", False, "the daemon's credentials could delete a raw/ object"))
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        out.append(("delete denied under raw/", code in ("AccessDenied", "AccessDeniedException"), code))
    return out


def flag(catalog: Catalog, config: AltairConfig, results: list[tuple[str, bool, str]]) -> bool:
    """Open or resolve S3_CONFIG_UNSAFE from `check` results. Returns safe?"""
    unsafe = [f"{label} ({detail})" for label, ok, detail in results if not ok and label in ("bucket versioning enabled", "delete denied under raw/")]
    with catalog.transaction() as tx:
        if unsafe:
            raise_issue(tx, config, kind="S3_CONFIG_UNSAFE", severity="blocking", fingerprint="S3_CONFIG_UNSAFE",
                        message="The S3 archive is not safe to rely on: " + "; ".join(unsafe) + ". All cleanup is stopped. "
                                "Run `altair storage s3 init` with an admin profile and use the IAM policy it prints.",
                        scope={"location": "s3"})
        else:
            resolve_issue(tx, config, "S3_CONFIG_UNSAFE", resolution="auto:doctor")
    return not unsafe


def policy_json(cfg: Location) -> str:
    return json.dumps(iam_policy(cfg), indent=2)
