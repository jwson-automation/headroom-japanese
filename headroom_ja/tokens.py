"""Token counter. Accurate with tiktoken, approximate without it.

Approximation: a CJK character is close to one token; ASCII/whitespace is
~4 chars per token. Whitespace is never counted as CJK. Install tiktoken for
exact savings numbers.
"""

from __future__ import annotations


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return (0x3040 <= o <= 0x30ff       # hiragana + katakana
            or 0x3400 <= o <= 0x9fff    # CJK ext-A + unified ideographs
            or 0xff66 <= o <= 0xff9f     # halfwidth katakana
            or 0x20000 <= o <= 0x2ffff)  # CJK ext-B and beyond


try:
    import tiktoken

    _enc = tiktoken.get_encoding("cl100k_base")

    def count_tokens(s: str) -> int:
        return len(_enc.encode(s))

    BACKEND = "tiktoken"

except Exception:  # tiktoken not installed

    def count_tokens(s: str) -> int:
        cjk = sum(1 for ch in s if not ch.isspace() and _is_cjk(ch))
        other = len(s) - cjk
        return max(1, cjk + other // 4)

    BACKEND = "approx"
