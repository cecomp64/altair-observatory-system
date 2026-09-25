"""Object-name normalisation, identical to the Hub's
Catalogue::AliasNormalizer: "M 31", "m31" and "M-31" are the same key, and
"NGC0224" matches "NGC 224"."""
from __future__ import annotations

import re
import unicodedata


def normalize(name: str | None) -> str | None:
    if name is None:
        return None
    key = unicodedata.normalize("NFKD", str(name)).lower()
    key = re.sub(r"[^a-z0-9+.]", "", key)
    key = re.sub(r"^([a-z]+)0+(?=\d)", r"\1", key)
    return key or None
