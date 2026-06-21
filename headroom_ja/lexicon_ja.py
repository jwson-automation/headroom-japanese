"""Japanese lexicon (the tuning surface).

Edit these lists to change compression behaviour:
- Function words the tokenizer splits on: particles / conjunctions / verb endings / punctuation
- Keywords used for error detection

Add or remove words to fit your data while you benchmark.
"""

# ── Function words used as delimiters by the tokenizer ──────────────

# Particles (助詞)
PARTICLES = [
    "が", "を", "に", "は", "へ", "と", "で", "も", "や", "の",
    "から", "まで", "より", "ので", "けど", "のに",
    "か", "ね", "よ", "わ", "ぞ", "な",
]

# Conjunctions (接続詞)
CONJUNCTIONS = [
    "そして", "それで", "しかし", "だから", "でも", "ですが",
    "ところが", "または", "および", "つまり", "なお", "ただし",
    "それから", "したがって",
]

# Verb endings / auxiliaries. Longest-match is handled at sort time.
ENDINGS = [
    "している", "させる", "される", "しました", "します",
    "して", "する", "した", "され", "させ",
    "ました", "ます", "でした", "です",
    "ない", "なかった", "たい", "欲しい", "ほしい",
    "だ", "た", "て",
]

# Punctuation / whitespace
PUNCT = [
    "、", "。", "，", "．", ",", ".", "・",
    "「", "」", "『", "』", "（", "）", "(", ")",
    "！", "？", "!", "?", " ", "　", "\n", "\t",
]


# ── Error-detection keywords (Japanese + English) ───────────────────
ERROR_KEYWORDS = [
    # Japanese
    "エラー", "失敗", "異常", "拒否", "却下", "例外",
    "タイムアウト", "見つかりません", "未検出", "不正", "無効",
    # English (for mixed logs)
    "error", "failed", "fail", "exception", "denied",
    "declined", "timeout", "invalid", "not found",
]
