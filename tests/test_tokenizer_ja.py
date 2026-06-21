from headroom_ja.tokenizer_ja import tokenize_ja, keywords


def test_splits_on_particles_and_endings():
    toks = tokenize_ja("新しいパソコンが欲しい、そして安いキーボードも探している")
    types = dict((t, ty) for t, ty in toks)
    assert types.get("が") == "particle"
    assert types.get("そして") == "conj"
    assert types.get("、") == "punct"
    # the long ending is captured whole (not broken into して + いる)
    assert "している" in [t for t, _ in toks]


def test_keywords_extracts_content_words():
    kw = keywords("カードが拒否されました")
    # 拒否 must surface as a keyword (され / ました removed)
    assert any("拒否" in k for k in kw)
    assert "が" not in kw


def test_relevance_overlap():
    a = keywords("注文が拒否されました")
    b = keywords("拒否された注文を見せて")
    assert a & b  # 拒否 / 注文 overlap
