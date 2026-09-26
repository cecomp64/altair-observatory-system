"""Windows-only behaviour, run on the Windows CI runner (the processing PC runs
Windows): the node key in Credential Manager, and night boundaries with the
tzdata package instead of a system timezone database."""
from __future__ import annotations

import sys
import uuid

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")


def test_node_key_from_windows_credential_manager(config, monkeypatch):
    import keyring
    from keyring.backends.Windows import WinVaultKeyring

    assert isinstance(keyring.get_keyring(), WinVaultKeyring)
    target = f"altair-hub-test-{uuid.uuid4().hex[:8]}"
    monkeypatch.delenv("ALTAIR_HUB_API_KEY", raising=False)
    hub = config.hub.model_copy(update={"credential_target": target})
    keyring.set_password(target, hub.node, "node-key-from-vault")
    try:
        assert hub.api_key() == "node-key-from-vault"
    finally:
        keyring.delete_password(target, hub.node)
    assert hub.api_key() is None


def test_night_uses_the_site_timezone_without_a_system_tz_database():
    from datetime import date

    from altair.ingest.headers import night_of, parse_date_obs

    # 06:10 UTC on the 25th is 23:10 on the 24th in Los Angeles: the night of the 24th.
    assert night_of(parse_date_obs("2026-09-25T06:10:02"), "America/Los_Angeles") == date(2026, 9, 24)
