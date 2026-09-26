"""include/exclude globs for rig files (SPEC §5: ``**/*.fits``, ``_altair/**``),
matched against POSIX-style paths relative to the raw root, ignoring case
(Windows file names are case-insensitive)."""
from __future__ import annotations

import re
from functools import lru_cache


@lru_cache(maxsize=256)
def _compile(pattern: str) -> re.Pattern[str]:
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z", re.IGNORECASE)


def matches(rel_path: str, pattern: str) -> bool:
    return bool(_compile(pattern).match(rel_path))


def selected(rel_path: str, include: list[str], exclude: list[str]) -> bool:
    return any(matches(rel_path, p) for p in include) and not any(matches(rel_path, p) for p in exclude)
