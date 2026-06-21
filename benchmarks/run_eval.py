"""Benchmark harness: measure token savings AND answer-quality retention.

Two axes (never report one without the other):
  - savings    : 1 - compressed/original  (free, deterministic)
  - quality    : can the LLM still answer from the compressed context?

Deterministic signal (no API spend): answer_kept — did the compressor keep the
item that holds the answer? Run with --no-llm to get only this.

LLM signal (spends your ANTHROPIC_API_KEY credits): ask the same question on the
full context vs the compressed context, then judge each against gold. The
headline metric is retention = mean(compressed_correct | full_correct).

    pip install -e '.[bench]'
    export ANTHROPIC_API_KEY=sk-ant-...        # a key you can rotate
    python -m benchmarks.run_eval               # full run + writes benchmark.md
    python -m benchmarks.run_eval --no-llm      # deterministic only, no spend
"""

from __future__ import annotations

import argparse
import json
import sys
import os
from datetime import datetime

from headroom_ja import __version__, compress
from headroom_ja.tokens import BACKEND

from . import datasets


def _answer_kept(text: str, answer_ids):
    """True if every answer-bearing item survived; None if aggregation (no single item).

    answer_ids holds the raw id VALUES (int or str), matched as they appear in JSON.
    """
    if not answer_ids:
        return None
    return all(f'"id": {json.dumps(v, ensure_ascii=False)}' in text for v in answer_ids)


def run(use_llm: bool, out_path: str | None, md_path: str | None):
    samples = datasets.build()
    rows = []

    for name, data, question, gold, answer_ids in samples:
        content = json.dumps(data, ensure_ascii=False)
        r = compress(content, query=question)
        kept = _answer_kept(r.text, answer_ids)

        row = {
            "dataset": name,
            "token_backend": BACKEND,
            "n_items": (r.kept + r.dropped) or (len(data) if isinstance(data, list) else 0),
            "question": question,
            "gold": gold,
            "original_tokens": r.original_tokens,
            "compressed_tokens": r.compressed_tokens,
            "ratio": round(r.ratio, 4),
            "kept": r.kept,
            "dropped": r.dropped,
            "answer_ids": answer_ids,
            "answer_kept": kept,
            "original_data": content,   # full input sent for the 'full' answer
            "compressed_text": r.text,  # exactly what the LLM saw for the 'compressed' answer
        }

        if use_llm:
            from . import models
            a_full, _ = models.answer(question, content)
            a_comp, retrieved = models.answer(question, r.text, cache_key=r.cache_key)
            j_full = models.judge(question, gold, a_full)
            j_comp = models.judge(question, gold, a_comp)
            row.update({
                "answer_full": a_full,
                "answer_compressed": a_comp,
                "full_correct": j_full["correct"],
                "compressed_correct": j_comp["correct"],
                "retrieve_called": retrieved,   # over-compression signal
                "quality_delta": int(j_comp["correct"]) - int(j_full["correct"]),
                "judge_model": models.JUDGE,
                "answerer_model": models.ANSWERER,
            })

        rows.append(row)
        _print_row(row, use_llm)

    _print_summary(rows, use_llm)

    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"\nwrote {len(rows)} rows -> {out_path}")

    if use_llm and md_path:
        md = _render_markdown(rows)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"wrote latest report -> {md_path}")
        # History: never overwrite — every run is kept as its own snapshot.
        os.makedirs("benchmarks/history", exist_ok=True)
        label = f"{__version__}_{datetime.now().strftime('%Y%m%d-%H%M')}"
        hist = f"benchmarks/history/{label}.md"
        with open(hist, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"wrote history snapshot -> {hist}")

    return rows


def _print_row(row, use_llm):
    kept = "n/a" if row["answer_kept"] is None else str(row["answer_kept"])
    base = (f"[{row['dataset']:<20}] {row['original_tokens']:>6} -> "
            f"{row['compressed_tokens']:>5} tok  ({row['ratio']:>5.0%})  kept={kept}")
    if use_llm:
        rtr = " +retrieve" if row.get("retrieve_called") else ""
        base += f"  full={row['full_correct']}  comp={row['compressed_correct']}{rtr}"
    print(base)


def _print_summary(rows, use_llm):
    n = len(rows)
    mean_ratio = sum(r["ratio"] for r in rows) / n
    kept_rows = [r for r in rows if r["answer_kept"] is not None]
    kept_rate = (sum(r["answer_kept"] for r in kept_rows) / len(kept_rows)
                 if kept_rows else 0)
    print("\n" + "=" * 64)
    print(f"samples           : {n}")
    print(f"mean savings      : {mean_ratio:.0%}")
    print(f"answer_kept rate  : {kept_rate:.0%}  ({len(kept_rows)} single-answer cases)")
    if use_llm:
        answerable = [r for r in rows if r["full_correct"]]
        if answerable:
            retention = sum(r["compressed_correct"] for r in answerable) / len(answerable)
            print(f"retention         : {retention:.0%}  "
                  f"(compressed correct, of {len(answerable)} answerable on full)")
        retr = [r for r in rows if r.get("retrieve_called")]
        print(f"retrieve used     : {len(retr)}/{len(rows)}  "
              f"({[r['dataset'] for r in retr]})")
        failures = [r for r in rows if r["full_correct"] and not r["compressed_correct"]]
        if failures:
            print(f"compression-caused failures: {[r['dataset'] for r in failures]}")
    print("=" * 64)


def _render_markdown(rows) -> str:
    n = len(rows)
    mean_ratio = sum(r["ratio"] for r in rows) / n
    kept_rows = [r for r in rows if r["answer_kept"] is not None]
    kept_rate = sum(r["answer_kept"] for r in kept_rows) / len(kept_rows) if kept_rows else 0
    answerable = [r for r in rows if r["full_correct"]]
    retention = (sum(r["compressed_correct"] for r in answerable) / len(answerable)
                 if answerable else 0)
    backend = rows[0]["token_backend"]
    answerer = rows[0].get("answerer_model", "?")
    judge = rows[0].get("judge_model", "?")
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    def yn(b):
        return "✅" if b else "❌"

    L = []
    L.append("# headroom-japanese — benchmark")
    L.append("")
    L.append(f"Generated: {stamp} · lib `v{__version__}` · answerer: `{answerer}` · "
             f"judge: `{judge}` · token backend: `{backend}`")
    L.append("")
    L.append("Each case asks the **same Japanese question twice** — once on the full "
             "tool output, once on the compressed output — and an LLM judge grades each "
             "answer against the gold answer. Transcripts are verbatim.")
    L.append("")
    L.append("## Summary")
    L.append("")
    L.append(f"- samples: **{n}**")
    L.append(f"- mean token savings: **{mean_ratio:.0%}**")
    L.append(f"- answer_kept (deterministic, {len(kept_rows)} single-answer cases): "
             f"**{kept_rate:.0%}**")
    L.append(f"- **retention** (compressed still correct, of {len(answerable)} answerable "
             f"on full context): **{retention:.0%}**")
    L.append("")
    L.append("| dataset | savings | full | compressed | retrieve | answer_kept |")
    L.append("|---|---|:---:|:---:|:---:|:---:|")
    for r in rows:
        kept = "n/a" if r["answer_kept"] is None else yn(r["answer_kept"])
        rtr = "🔄" if r.get("retrieve_called") else ""
        L.append(f"| {r['dataset']} | {r['ratio']:.0%} | {yn(r['full_correct'])} | "
                 f"{yn(r['compressed_correct'])} | {rtr} | {kept} |")
    L.append("")
    L.append("## Transcripts")
    L.append("")
    L.append("Each case shows the **full original data** (what the `full` answer saw), "
             "the **exact compressed text** sent to the model (what the `compressed` "
             "answer saw), and both answers verbatim.")
    L.append("")
    for r in rows:
        L.append(f"### `{r['dataset']}`  ({r['n_items']} items)")
        L.append("")
        L.append(f"- **質問 (question)**: {r['question']}")
        L.append(f"- **正解 (gold)**: `{r['gold']}`")
        L.append(f"- **トークン**: {r['original_tokens']} → {r['compressed_tokens']} "
                 f"(**{r['ratio']:.0%}** saved · kept {r['kept']} / dropped {r['dropped']})")
        kept = "n/a (集計のため単一item無し)" if r["answer_kept"] is None else str(r["answer_kept"])
        L.append(f"- **answer_kept**: {kept}")
        L.append(f"- **retrieve呼び出し**: {'はい (省略された元データを取り戻して計算)' if r.get('retrieve_called') else 'いいえ (圧縮テキストだけで回答)'}")
        L.append("")
        L.append("**回答 (answers):**")
        L.append("")
        L.append("| context | 回答 | 判定 |")
        L.append("|---|---|:---:|")
        L.append(f"| 原本 (full, {r['original_tokens']}tok) | {r['answer_full']} | "
                 f"{yn(r['full_correct'])} |")
        L.append(f"| 圧縮 (compressed, {r['compressed_tokens']}tok) | {r['answer_compressed']} | "
                 f"{yn(r['compressed_correct'])} |")
        L.append("")
        L.append("**送信した圧縮テキスト (compressed text the model actually saw):**")
        L.append("")
        L.append("```json")
        L.append(r["compressed_text"])
        L.append("```")
        L.append("")
        L.append(f"<details><summary>元データ全体 (full original data, {r['n_items']} items)</summary>")
        L.append("")
        L.append("```json")
        L.append(r["original_data"])
        L.append("```")
        L.append("")
        L.append("</details>")
        L.append("")

    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-llm", action="store_true",
                    help="deterministic only (answer_kept + savings), no API spend")
    ap.add_argument("--out", default="benchmarks/results/latest.jsonl",
                    help="JSONL output path (set empty to skip)")
    ap.add_argument("--md", default="benchmark.md",
                    help="markdown report path (LLM runs only)")
    args = ap.parse_args()

    if not args.no_llm:
        import os
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("ANTHROPIC_API_KEY not set. Export it (a key you can rotate) or "
                  "run with --no-llm.", file=sys.stderr)
            sys.exit(2)
        os.makedirs("benchmarks/results", exist_ok=True)

    run(use_llm=not args.no_llm, out_path=args.out or None, md_path=args.md or None)


if __name__ == "__main__":
    main()
