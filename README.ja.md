# headroom-japanese

[English](README.md) · **日本語** · [한국어](README.ko.md)

[headroom](https://github.com/chopratejas/headroom) の **コアアイデア**（ContentRouter + SmartCrusher + CCR）を、
形態素解析器なしの **ルールベース** でゼロから再実装した、**日本語**向けコンテキスト圧縮ツール。
データとクエリはどちらも日本語を前提とする。バージョン管理とベンチマークを自分で回す前提で作っている。

> headroom 本体はコアが Rust に移植されており、表面積が大きい。本プロジェクトは最小限のコアだけを、
> **純粋な Python と標準ライブラリのみ** で持ち込み、日本語に合わせて調整している。

## 仕組み（LLM に渡す前に配列を縮める6ステップ）

| # | ルール | 方法 | 日本語対応 |
|---|--------|------|-----------|
| 1 | 重複の削除 | MD5 ハッシュが同じなら1つだけ残す | 言語非依存 |
| 2 | **エラー保持** | キーワード包含チェック | `lexicon_ja.py` の日本語キーワード |
| 3 | 数値の外れ値 | フィールドごとの z スコア > 2σ | 言語非依存 |
| 4 | 構造の外れ値 | 稀なフィールドを持つ項目 | 言語非依存 |
| 5 | **クエリ関連性** | キーワードの重なり | ルールベース日本語トークナイザ |
| 6 | 先頭/末尾 + 補充 | 位置 + 均等サンプリング | 言語非依存 |

重要項目（2〜5）は **予算を超えても保持する**（品質保証）。
破棄した原本は CCR キャッシュに残り、`retrieve(key, query)` で取り戻せる。

## 日本語トークナイザ（形態素解析器なし）

機能語（助詞・接続詞・動詞語尾・句読点）を **区切り** として使い、その間の内容語をキーワードとする。

```
新しいパソコンが欲しい、そして安いキーボードも探している
→ keyword: 新しいパソコン / 安いキーボード / 探
  particle: が / も   ending: 欲しい / している   conj: そして
```

制限: 単語の中に文字として埋め込まれた助詞は誤分割される（例: `長い` の が）。
漢字名詞が中心のデータならリスクは低い。精度が必要なら `pip install '.[accurate]'` で `fugashi` に差し替える。

## インストール / 実行

```bash
cd headroom-japanese
pip install -e .              # コアのみ（依存なし）
pip install -e '.[accurate]'  # tiktoken（正確なトークン数）+ fugashi（形態素）

python examples/demo.py       # 500件 -> 圧縮デモ
pytest                        # テスト
```

## 使い方

```python
from headroom_ja import compress, retrieve

r = compress(tool_output_json, query="拒否された注文はある？")
print(r)               # [json] 18000 -> 1200 tok (93% saved, 15 kept / 485 dropped)
send_to_llm(r.text)

# LLM がもっと必要なとき
more = retrieve(r.cache_key, query="拒否")
```

## チューニング箇所

- `headroom_ja/lexicon_ja.py` — 助詞 / 接続詞 / 語尾 / エラーキーワードのリスト。
  **ここをデータに合わせて調整する。**
- `CrusherConfig` — `max_items`、`variance_threshold`、`first/last_fraction` など。

## ベンチマーク

`CompressResult` だけを記録すればバージョン比較できる:
`(content_type, original_tokens, compressed_tokens, kept, dropped, ratio)`。
あわせて `retrieve` の呼び出し頻度（= 過圧縮のサイン）を見ると、削減率と品質のトレードオフが分かる。

## ライセンス

Apache-2.0（上流の headroom と同じ）。
