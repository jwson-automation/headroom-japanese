"""Rule-based Japanese tokenizer (no morphological analyzer).

Principle: a Japanese sentence = content word + function word + content word ...
    Cut whenever a function word (particle / conjunction / verb ending /
    punctuation) appears. Whatever sits between is a keyword.

Limitation: a particle embedded inside a word as a bare character gets
    mis-split (e.g. the が in 長い). Risk is low for kanji-noun-heavy data.
    Swap in fugashi if you need real accuracy.
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


def keywords(text: str) -> set[str]:
    """Return only content words (keywords). Used for relevance matching."""
    return {t for t, ty in tokenize_ja(text) if ty == "keyword" and t.strip()}
