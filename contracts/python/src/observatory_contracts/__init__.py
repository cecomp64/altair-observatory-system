"""The Hub API contract as pydantic models.

The models under ``observatory_contracts.models`` are generated from
``contracts/schemas`` by ``tools/generate_contracts.py``; never edit them by hand.
The rig agent (``robs``) and the processing core (``altair``) build and parse Hub
payloads with them, so all three components share one definition of the API.
"""

API_REVISION = 2
"""The newest ``api_revision`` these models describe (see contracts/CHANGELOG.md)."""


def check_hub_revision(hub_revision: int | None) -> tuple[bool, str]:
    """Is a Hub speaking ``hub_revision`` usable by a client built on these models?

    Revisions within ``/api/v1`` only add to the API, except where
    contracts/CHANGELOG.md says otherwise, so a newer Hub is fine and an older one
    may lack what the client relies on. Returns ``(ok, explanation)`` for
    ``robs check-config`` and ``altair doctor``.
    """
    if hub_revision is None:
        return False, "the Hub did not report its api_revision (it predates api_revision 2; upgrade the Hub)"
    if hub_revision < API_REVISION:
        return False, f"the Hub speaks api_revision {hub_revision}, older than this client's {API_REVISION}; upgrade the Hub"
    if hub_revision > API_REVISION:
        return True, f"the Hub speaks api_revision {hub_revision}, newer than this client's {API_REVISION} (compatible)"
    return True, f"api_revision {hub_revision}"


__all__ = ["API_REVISION", "check_hub_revision"]
