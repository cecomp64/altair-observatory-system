"""The Hub API contract as pydantic models.

The models under ``observatory_contracts.models`` are generated from
``contracts/schemas`` by ``tools/generate_contracts.py``; never edit them by hand.
The rig agent (``robs``) and the processing core (``altair``) build and parse Hub
payloads with them, so all three components share one definition of the API.
"""

API_REVISION = 1
"""The newest ``api_revision`` these models describe (see contracts/CHANGELOG.md)."""

__all__ = ["API_REVISION"]
