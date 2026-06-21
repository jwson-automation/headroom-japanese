"""Token counter. Accurate with tiktoken, approximate without it.

Approximation: a CJK character is close to one token; ASCII/whitespace is
~4 chars per token. Install tiktoken for exact savings numbers.
"""

from __future__ import annotations

try:
    import tiktoken

    _enc = tiktoken.get_encoding("cl100k_base")

    def count_tokens(s: str) -> int:
        return len(_enc.encode(s))

    BACKEND = "tiktoken"

except Exception:  # tiktoken not installed

    def count_tokens(s: str) -> int:
        cjk = sum(1 for ch in s if "　" <= ch <= "鿿" or "＀" <= ch <= "￯")
        other = len(s) - cjk
        return max(1, cjk + other // 4)

    BACKEND = "approx"
