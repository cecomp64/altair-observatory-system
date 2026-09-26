from __future__ import annotations

import pytest
import responses

from robs.api_client import ApiError, ObservatoryApiClient


@pytest.fixture
def api():
    return ObservatoryApiClient("https://example.test", "test-token")


@responses.activate
def test_active_targets_sends_bearer_token_and_parses_response(api):
    responses.get(
        "https://example.test/api/v1/telescopes/test-scope/active_targets",
        json={"telescope": {"id": 1}, "targets": [{"id": 5, "name": "M42"}]},
    )

    targets = api.active_targets("test-scope")

    assert targets == [{"id": 5, "name": "M42"}]
    assert responses.calls[0].request.headers["Authorization"] == "Bearer test-token"


@responses.activate
def test_raises_api_error_on_failure_response(api):
    responses.get(
        "https://example.test/api/v1/telescopes/test-scope/active_targets",
        json={"error": "Invalid or missing API key"},
        status=401,
    )

    with pytest.raises(ApiError) as exc_info:
        api.active_targets("test-scope")

    assert exc_info.value.status_code == 401
    assert "Invalid or missing API key" in str(exc_info.value)


@responses.activate
def test_update_progress_sends_expected_payload(api):
    responses.patch(
        "https://example.test/api/v1/targets/5/progress",
        json={"ok": True},
    )

    api.update_progress(5, [{"id": 1, "completed_count": 10}], status="in_progress")

    request = responses.calls[0].request
    assert request.body is not None
    import json

    assert json.loads(request.body) == {
        "exposure_plans": [{"id": 1, "completed_count": 10}],
        "status": "in_progress",
    }

