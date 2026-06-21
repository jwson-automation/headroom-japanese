"""Rule-based Japanese tokenizer (no morphological analyzer).

Principle: a Japanese sentence = content word + function word + content word ...
    Cut whenever a function word (particle / conjunction / verb ending /
    punctuation) appears. Whatever sits between is a keyword.

Over-splitting guard: a single-character hiragana particle (が, に, は, で, ...)
    also appears *inside* content words (はな, たかい, 名前). We only treat such a
    particle as a delimiter when the preceding character is a "hard" content
    char (kanji or katakana) -- i.e. it follows a real word. Multi-char function
    words, punctuation, and を always split. This under-splits pure-hiragana
    words rather than destroying them. Swap in fugashi for full accuracy.
"""

from __future__ import annotations

from .lexicon_ja import PARTICLES, CONJUNCTIONS, ENDINGS, PUNCT

# Match longer function words first so "している" is not broken into "して" + "いる".
_STOP = sorted(set(PARTICLES + CONJUNCTIONS + ENDINGS + PUNCT), key=len, reverse=True)
_TYPE: dict[str, str] = {}
for _w in PUNCT:
    _TYPE[_w] = "punct"
for _w in ENDINGS:
    _TYPE.setdefault(_w, "ending")
for _w in CONJUNCTIONS:
    _TYPE.setdefault(_w, "conj")
for _w in PARTICLES:
    _TYPE.setdefault(_w, "particle")


def _is_hiragana(ch: str) -> bool:
    return "぀" <= ch <= "ゟ"


def _is_hard(ch: str) -> bool:
    """A content boundary: kanji or katakana. A particle right after this is grammatical."""
    o = ord(ch)
    return (0x4e00 <= o <= 0x9fff      # CJK unified ideographs
            or 0x3400 <= o <= 0x4dbf   # CJK extension A
            or 0x30a0 <= o <= 0x30ff   # katakana
            or 0xff66 <= o <= 0xff9f)  # halfwidth katakana


# Single-char hiragana function words that also occur inside words.
# を is excluded: it is essentially always the object particle, never word-internal.
_SINGLE_KANA = {w for w in (PARTICLES + ENDINGS)
                if len(w) == 1 and _is_hiragana(w)} - {"を"}


def tokenize_ja(text: str) -> list[tuple[str, str]]:
    """Split text into (token, type) pairs.

    type: keyword | particle | conj | ending | punct
    Function words are returned too (tagged), not discarded.
    """
    out: list[tuple[str, str]] = []
    buf = ""
    i = 0
    n = len(text)
    while i < n:
        hit = next((w for w in _STOP if text.startswith(w, i)), None)
        if hit:
            # Dangerous single-kana particle glued inside a word -> treat as content.
            if hit in _SINGLE_KANA and not (buf and _is_hard(buf[-1])):
                buf += hit
                i += 1
                continue
            if buf:
                out.append((buf, "keyword"))
                buf = ""
            out.append((hit, _TYPE[hit]))
            i += len(hit)
        else:
            buf += text[i]
            i += 1
    if buf:
        out.append((buf, "keyword"))
    return out


def _char_class(ch: str) -> str:
    o = ord(ch)
    if 0x3040 <= o <= 0x309f:
        return "H"  # hiragana
    if 0x30a0 <= o <= 0x30ff or 0xff66 <= o <= 0xff9f:
        return "A"  # katakana
    if 0x4e00 <= o <= 0x9fff or 0x3400 <= o <= 0x4dbf:
        return "K"  # kanji
    if ch.isascii() and ch.isalnum():
        return "L"  # latin / digit
    return "O"


def _subtokens(s: str) -> list[str]:
    """Split a keyword at script boundaries so 返品ステータス -> [返品, ステータス].

    Okurigana is preserved: a hiragana run directly after a kanji run joins it
    (食べる stays whole, 新しい stays whole), but katakana / latin runs always
    break off into their own tokens.
    """
    runs: list[list[str]] = []
    for ch in s:
        c = _char_class(ch)
        if runs and runs[-1][0] == c:
            runs[-1][1] += ch
        else:
            runs.append([c, ch])
    toks: list[str] = []
    last: str | None = None
    for c, text in runs:
        if c == "H" and toks and last == "K":
            toks[-1] += text  # okurigana joins its kanji stem; stays kanji-class
        else:
            toks.append(text)
            last = c
    return toks


def keywords(text: str) -> set[str]:
    """Return content words (keywords) for relevance matching.

    Splits glued cross-script words at script boundaries and drops stray single
    hiragana characters left over from grammatical splitting.
    """
    out: set[str] = set()
    for t, ty in tokenize_ja(text):
        if ty != "keyword":
            continue
        for sub in _subtokens(t):
            sub = sub.strip().lower()  # lower-case latin so VIP == vip; CJK unaffected
            if not sub:
                continue
            if len(sub) == 1 and _is_hiragana(sub):  # leftover grammatical noise
                continue
            out.add(sub)
    return out
