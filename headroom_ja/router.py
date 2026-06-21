"""ContentRouter: classifies the content type only (no compression).

v1 is heuristic (regex) only. No ML (Magika).
Patterns are tweaked slightly to also catch Japanese logs / search results.
"""

from __future__ import annotations

import json
import re

_RE_SEARCH = re.compile(r"^[\w./\-]+:\d+:", re.M)
_RE_TS = re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}")
_RE_LOGLEVEL = re.compile(r"\b(ERROR|WARN|INFO|DEBUG|FATAL)\b|エラー|警告|情報")


def detect(content: str) -> str:
    """Returns: json | search | diff | log | text"""
    s = content.strip()
    if not s:
        return "text"

    # 1) JSON
    if s[:1] in "[{":
        try:
            json.loads(s)
            return "json"
        except (json.JSONDecodeError, ValueError):
            pass

    # 2) Search results (file:line:)
    if _RE_SEARCH.search(s):
        return "search"

    # 3) Unified diff
    if s.startswith(("diff --git", "--- ", "@@ ")):
        return "diff"

    # 4) Logs (timestamp + level)
    if _RE_TS.search(s) and _RE_LOGLEVEL.search(s):
        return "log"

    return "text"
