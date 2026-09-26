"""The shared CLI context: the config, the catalog and the storage and Hub
handles each `altair` command needs, created on first use."""
from __future__ import annotations

import os

import click

from altair.catalog.db import Catalog
from altair.config import AltairConfig, load

DEFAULT_CONFIG = os.environ.get("ALTAIR_CONFIG", "C:/ProgramData/Altair/altair.yaml")


class Ctx:
    def __init__(self, config_path: str):
        self.config_path = config_path
        self._config: AltairConfig | None = None
        self._catalog: Catalog | None = None
        self._s3 = None

    @property
    def config(self) -> AltairConfig:
        if self._config is None:
            self._config = load(self.config_path)
        return self._config

    @property
    def catalog(self) -> Catalog:
        if self._catalog is None:
            self._catalog = Catalog(self.config.catalog_path)
        return self._catalog

    def client(self):
        from altair.hub.client import HubClient

        hub = self.config.hub
        if not hub.enabled:
            raise click.ClickException("hub.enabled is false in altair.yaml")
        key = hub.api_key()
        if not key:
            raise click.ClickException(f"No Hub API key: set ALTAIR_HUB_API_KEY or store it in Windows Credential Manager as {hub.credential_target!r}")
        return HubClient(hub.base_url, key)

    def sync(self):
        from altair.hub.sync import HubSync

        return HubSync(self.catalog, self.config, self.client())

    def hub_config(self):
        from altair.hub.config_sync import load_cached

        return load_cached(self.catalog) if self.config.hub.enabled else None

    def nas(self):
        from altair.storage.locations import nas_location

        nas = nas_location(self.config)
        if nas is None:
            raise click.ClickException("altair.yaml has no storage location named 'nas' with a root")
        return nas

    def s3(self, required: bool = True):
        from altair.storage.locations import s3_location

        if self._s3 is None:
            self._s3 = s3_location(self.config)
        if self._s3 is None and required:
            raise click.ClickException("altair.yaml has no S3 storage location with a bucket")
        return self._s3


pass_ctx = click.make_pass_decorator(Ctx)


def check_line(label: str, passed: bool, detail: str = "") -> bool:
    click.echo(f"[{'ok' if passed else 'FAIL'}] {label}{': ' + detail if detail else ''}")
    return passed


def human_bytes(n: float | None) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TB"
