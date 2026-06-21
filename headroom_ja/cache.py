"""CCR-lite: keep the dropped originals and fetch them back by key.

This makes lossy compression safe. On compression we store the original
array; if the LLM decides it needs more, it calls retrieve(key, query).
"""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict

from .tokenizer_ja import keywords


class CCRStore:
    def __init__(self, max_entries: int = 1000):
        self.max_entries = max_entries
        self._store: "OrderedDict[str, list]" = OrderedDict()

    def put(self, items: list) -> str:
        """Store the original array and return its key."""
        blob = json.dumps(items, ensure_ascii=False, sort_keys=True)
        key = hashlib.md5(blob.encode("utf-8")).hexdigest()[:12]
        if key in self._store:
            self._store.move_to_end(key)
        else:
            self._store[key] = items
            if len(self._store) > self.max_entries:
                self._store.popitem(last=False)  # LRU eviction
        return key

    def retrieve(self, key: str, query: str | None = None, limit: int = 50) -> list:
        """Fetch the original by key. With a query, keep only keyword-overlapping items."""
        items = self._store.get(key)
        if items is None:
            return []
        if not query:
            return items[:limit]
        q = keywords(query)
        hits = [
            it for it in items
            if q & keywords(json.dumps(it, ensure_ascii=False))
        ]
        return hits[:limit]
