from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest
from astropy.io import fits
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from observatory_contracts import API_REVISION

from altair.catalog.db import Catalog
from altair.config import AltairConfig
from altair.hub.client import HubClient

SCHEMAS = Path(__file__).resolve().parents[2] / "contracts" / "schemas"
_REGISTRY = Registry().with_resources(
    (s["$id"], Resource.from_contents(s)) for s in (json.loads(p.read_text()) for p in SCHEMAS.rglob("*.json"))
)


def contract_errors(schema: str, data: Any) -> list[str]:
    validator = Draft202012Validator(json.loads((SCHEMAS / schema).read_text()), registry=_REGISTRY,
                                     format_checker=Draft202012Validator.FORMAT_CHECKER)
    return [f"{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in validator.iter_errors(data)]


TELESCOPE = "backyard-16in"
TRAIN = "esprit100_2600mm"


def hub_config_payload(targets: list[dict] | None = None) -> dict:
    return {
        "api_revision": API_REVISION,
        "node": {"name": "altair-proc-01"},
        "telescopes": [{
            "slug": TELESCOPE, "timezone": "America/Los_Angeles", "latitude": 37.3, "longitude": -121.9, "elevation_m": 120,
            "optical_trains": [{
                "key": TRAIN, "camera_type": "mono", "focal_length_mm": 550, "pixel_size_um": 3.76,
                "sensor_width_px": 6248, "sensor_height_px": 4176, "has_rotator": True,
                "filters": [{"name": "Ha", "aliases": ["H-alpha", "HA"]}, {"name": "OIII", "aliases": ["O3"]}],
                "header_aliases": {"telescope": ["Esprit 100ED"], "camera": ["ZWO ASI2600MM Pro"]},
            }],
        }],
        "targets": targets if targets is not None else [
            {"id": 34, "project_id": 12, "telescope": TELESCOPE, "optical_train": TRAIN, "name": "M31", "nina_name": "#34 M31",
             "status": "active", "ra_deg": 10.68471, "dec_deg": 41.26875, "rotation_deg": None,
             "aliases": ["M31", "M 31", "NGC 224", "Andromeda Galaxy"], "processing_settings": {"multi_night": {"enabled": True, "mode": "master_merge"}}},
            {"id": 35, "project_id": 12, "telescope": TELESCOPE, "optical_train": None, "name": "M33", "nina_name": "#35 M33",
             "status": "active", "ra_deg": 23.4621, "dec_deg": 30.6599, "rotation_deg": None, "aliases": ["M33", "Triangulum Galaxy"],
             "processing_settings": {}},
            {"id": 36, "project_id": 13, "telescope": TELESCOPE, "optical_train": TRAIN, "name": "Draft thing", "nina_name": "#36 Draft thing",
             "status": "draft", "ra_deg": 100.0, "dec_deg": 10.0, "rotation_deg": None, "aliases": [], "processing_settings": {}},
        ],
        "equipment_events": [{"id": 7, "optical_train": TRAIN, "at": "2026-09-20T18:00:00Z", "kind": "sensor_cleaned", "filter": None, "note": "dust"}],
    }


class FakeHub:
    """A stateful, contract-checking stand-in for the Hub API."""

    def __init__(self, config: dict | None = None):
        self.config = config or hub_config_payload()
        self.frames: dict[str, dict] = {}
        self.manual: dict[str, int] = {}
        self.nights: dict[tuple[str, str], dict] = {}
        self.issues: dict[str, dict] = {}
        self.jobs: dict[int, dict] = {}
        self.masters: dict[int, dict] = {}
        self.products: dict[tuple[str, int], dict] = {}
        self.commands: list[dict] = []
        self.acks: dict[int, dict] = {}
        self.heartbeats: list[dict] = []
        self.requests: list[tuple[str, str]] = []
        self.contract_violations: list[str] = []
        self.fail_next: list[Any] = []  # exceptions or status codes to inject
        self.etag = '"v1"'

    def client(self) -> HubClient:
        return HubClient("https://hub.test", "key", transport=httpx.MockTransport(self.handle))

    def check(self, schema: str, data: Any) -> None:
        self.contract_violations += [f"{schema}: {e}" for e in contract_errors(schema, data)]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, request.url.path))
        if self.fail_next:
            failure = self.fail_next.pop(0)
            if isinstance(failure, Exception):
                raise failure
            if failure:
                return httpx.Response(failure, json={"error": "injected"})
        path = request.url.path.removeprefix("/api/v1")
        method = request.method
        body = json.loads(request.content) if request.headers.get("content-type", "").startswith("application/json") else None

        if method == "GET" and path == "/processing/config":
            if request.headers.get("if-none-match") == self.etag:
                return httpx.Response(304)
            return httpx.Response(200, json=self.config, headers={"ETag": self.etag})
        if path == "/processing/frames:batch":
            schema = "processing/frames_batch.request.json" if method == "POST" else "processing/frames_patch.request.json"
            self.check(schema, body)
            results = []
            for f in body["frames"]:
                if method == "POST":
                    stored = {**f}
                    if f["sha256"] in self.manual:
                        stored["target_id"] = self.manual[f["sha256"]]
                    self.frames[f["sha256"]] = stored
                    results.append({"sha256": f["sha256"], "id": len(self.frames), "target_id": stored["target_id"], "exposure_plan_id": None, "status": "ok"})
                elif f["sha256"] in self.frames:
                    self.frames[f["sha256"]].update({k: v for k, v in f.items() if k != "sha256"})
                    results.append({"sha256": f["sha256"], "id": 1, "target_id": self.frames[f["sha256"]]["target_id"], "status": "ok"})
                else:
                    results.append({"sha256": f["sha256"], "status": "error", "error": "unknown frame"})
            response = {"results": results}
            self.check("processing/frames_batch.response.json", response)
            return httpx.Response(200, json=response)
        if m := re.fullmatch(r"/processing/nights/([^/]+)/(\d{4}-\d{2}-\d{2})/digest", path):
            rows = [f for f in self.frames.values() if f["optical_train"] == m[1] and f["night"] == m[2]]
            xor = bytes(32)
            for f in rows:
                xor = bytes(a ^ b for a, b in zip(xor, bytes.fromhex(f["sha256"])))
            by_type: dict[str, int] = {}
            for f in rows:
                by_type[f["image_type"]] = by_type.get(f["image_type"], 0) + 1
            return httpx.Response(200, json={"frame_count": len(rows), "sha256_xor": xor.hex(), "by_type": by_type})
        if m := re.fullmatch(r"/processing/nights/([^/]+)/(\d{4}-\d{2}-\d{2})", path):
            self.check("processing/night.request.json", body)
            self.nights[(m[1], m[2])] = body
            return httpx.Response(200, json={"ok": True})
        if m := re.fullmatch(r"/processing/issues/(.+)", path):
            self.check("processing/issue.request.json", body)
            from urllib.parse import unquote

            self.issues[unquote(m[1])] = body
            return httpx.Response(200, json={"ok": True})
        if m := re.fullmatch(r"/processing/jobs/(\d+)", path):
            self.check("processing/job.request.json", body)
            self.jobs[int(m[1])] = body
            return httpx.Response(200, json={"ok": True})
        if m := re.fullmatch(r"/processing/calibration_masters/(\d+)", path):
            self.check("processing/calibration_master.request.json", body)
            self.masters[int(m[1])] = body
            return httpx.Response(200, json={"ok": True})
        if m := re.fullmatch(r"/processing/data_products/([a-z_]+)/(\d+)", path):
            content_type = request.headers["content-type"]
            parts = _multipart(request.content, content_type)
            metadata = json.loads(parts["metadata"])
            self.check("processing/data_product.metadata.json", metadata)
            self.products[(m[1], int(m[2]))] = {"metadata": metadata, "files": sorted(k for k in parts if k != "metadata")}
            return httpx.Response(200, json={"ok": True})
        if method == "GET" and path == "/processing/commands":
            pending = [c for c in self.commands if c.get("_state", "pending") == "pending"]
            for c in pending:
                c["_state"] = "delivered"
            out = [{k: v for k, v in c.items() if not k.startswith("_")} for c in pending]
            self.check("processing/commands.response.json", out)
            return httpx.Response(200, json=out)
        if m := re.fullmatch(r"/processing/commands/(\d+)/ack", path):
            self.check("processing/command_ack.request.json", body)
            self.acks[int(m[1])] = body
            return httpx.Response(200, json={"ok": True})
        if path == "/heartbeat":
            self.check("shared/heartbeat.request.json", body)
            self.heartbeats.append(body)
            return httpx.Response(200, json={"ok": True, "api_revision": 1})
        return httpx.Response(404, json={"error": f"no fake route {method} {path}"})

    def queue_command(self, command_id: int, kind: str, payload: dict) -> None:
        self.commands.append({"id": command_id, "kind": kind, "payload": payload, "created_at": "2026-09-25T12:41:01Z"})


def _multipart(content: bytes, content_type: str) -> dict[str, bytes]:
    boundary = content_type.split("boundary=")[1].encode()
    parts = {}
    for chunk in content.split(b"--" + boundary):
        if b"name=\"" not in chunk:
            continue
        head, _, data = chunk.partition(b"\r\n\r\n")
        name = re.search(rb'name="([^"]+)"', head).group(1).decode()
        parts[name] = data.rstrip(b"\r\n")
    return parts


@pytest.fixture
def config(tmp_path: Path) -> AltairConfig:
    return AltairConfig.model_validate({
        "site": {"name": "Backyard", "latitude": 37.3, "longitude": -121.9, "timezone": "America/Los_Angeles"},
        "paths": {"state": str(tmp_path / "state")},
        "rigs": {"esprit": {"hub": {"telescope": TELESCOPE, "optical_train": TRAIN}, "telescope": "esprit100", "camera": "asi2600mm",
                            "focal_length_mm": 550, "rotator": {"present": True, "keywords": ["ROTATOR"], "units": "steps"}}},
        "aliases": {"telescope": {"Esprit 100ED|ESPRIT100": "esprit100"}, "camera": {"ZWO ASI2600MM Pro": "asi2600mm"},
                    "filter": {"^(Ha|H-alpha)$": "Ha"}},
        "cameras": {"asi2600mm": {"type": "mono"}},
        "hub": {"enabled": True, "base_url": "https://hub.test", "node": "altair-proc-01"},
    })


@pytest.fixture
def catalog(config: AltairConfig) -> Catalog:
    cat = Catalog(config.catalog_path)
    yield cat
    cat.close()


@pytest.fixture
def fake_hub() -> FakeHub:
    return FakeHub()


def write_fits(path: Path, *, imagetyp="LIGHT", object_="#34 M31", filter_="H-alpha", exposure=300.0, date_obs="2026-09-25T06:10:02",
               ra=10.69, dec=41.27, telescop="Esprit 100ED", instrume="ZWO ASI2600MM Pro", rotator=31250, seed=0, **extra) -> Path:
    data = np.random.default_rng(seed).normal(1000, 20, (40, 60)).astype(np.float32)
    header = fits.Header()
    for key, value in {"IMAGETYP": imagetyp, "OBJECT": object_, "FILTER": filter_, "EXPTIME": exposure, "DATE-OBS": date_obs,
                       "RA": ra, "DEC": dec, "TELESCOP": telescop, "INSTRUME": instrume, "ROTATOR": rotator, "GAIN": 100,
                       "OFFSET": 50, "XBINNING": 1, "YBINNING": 1, "CCD-TEMP": -10.0, "FOCALLEN": 550, **extra}.items():
        if value is not None:
            header[key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    fits.writeto(path, data, header, overwrite=True)
    return path


@pytest.fixture
def s3_client():
    """A moto S3 with the archive bucket (versioning + Object Lock), as SPEC §7.5 sets it up."""
    import boto3
    from moto import mock_aws

    with mock_aws():
        client = boto3.client("s3", region_name="us-west-2")
        client.create_bucket(Bucket="astro-archive", CreateBucketConfiguration={"LocationConstraint": "us-west-2"},
                             ObjectLockEnabledForBucket=True)
        yield client


@pytest.fixture(autouse=True)
def _wall_clock():
    """Tests that drive time call altair.catalog.db.use_clock; restore it after."""
    from altair.catalog import db

    yield
    db.use_clock(None)
