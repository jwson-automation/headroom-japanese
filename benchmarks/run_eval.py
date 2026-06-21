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
from datetime import datetime

from headroom_ja import compress
from headroom_ja.tokens import BACKEND

from . import datasets


def _answer_kept(text: str, answer_ids: list[int]):
    """True if every answer-bearing item survived; None if aggregation (no single item)."""
    if not answer_ids:
        return None
    return all(f'"id": {i}' in text for i in answer_ids)


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
            "n_items": len(data) if isinstance(data, list) else len(data.get("results", [])),
            "question": question,
            "gold": gold,
            "original_tokens": r.original_tokens,
            "compressed_tokens": r.compressed_tokens,
            "ratio": round(r.ratio, 4),
            "kept": r.kept,
            "dropped": r.dropped,
            "answer_ids": answer_ids,
            "answer_kept": kept,
        }

        if use_llm:
            from . import models
            a_full = models.answer(question, content)
            a_comp = models.answer(question, r.text)
            j_full = models.judge(question, gold, a_full)
            j_comp = models.judge(question, gold, a_comp)
            row.update({
                "answer_full": a_full,
                "answer_compressed": a_comp,
                "full_correct": j_full["correct"],
                "compressed_correct": j_comp["correct"],
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
        _write_markdown(rows, md_path)
        print(f"wrote report -> {md_path}")

    return rows


def _print_row(row, use_llm):
    kept = "n/a" if row["answer_kept"] is None else str(row["answer_kept"])
    base = (f"[{row['dataset']:<20}] {row['original_tokens']:>6} -> "
            f"{row['compressed_tokens']:>5} tok  ({row['ratio']:>5.0%})  kept={kept}")
    if use_llm:
        base += f"  full={row['full_correct']}  comp={row['compressed_correct']}"
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
        failures = [r for r in rows if r["full_correct"] and not r["compressed_correct"]]
        if failures:
            print(f"compression-caused failures: {[r['dataset'] for r in failures]}")
    print("=" * 64)


def _write_markdown(rows, path):
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
    L.append(f"Generated: {stamp} · answerer: `{answerer}` · judge: `{judge}` · "
             f"token backend: `{backend}`")
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
    L.append("| dataset | savings | full | compressed | answer_kept |")
    L.append("|---|---|:---:|:---:|:---:|")
    for r in rows:
        kept = "n/a" if r["answer_kept"] is None else yn(r["answer_kept"])
        L.append(f"| {r['dataset']} | {r['ratio']:.0%} | {yn(r['full_correct'])} | "
                 f"{yn(r['compressed_correct'])} | {kept} |")
    L.append("")
    L.append("## Transcripts")
    L.append("")
    for r in rows:
        L.append(f"### `{r['dataset']}`  ({r['n_items']} items)")
        L.append("")
        L.append(f"- **質問**: {r['question']}")
        L.append(f"- **正解 (gold)**: `{r['gold']}`")
        L.append(f"- **トークン**: {r['original_tokens']} → {r['compressed_tokens']} "
                 f"(**{r['ratio']:.0%}** saved · kept {r['kept']} / dropped {r['dropped']})")
        kept = "n/a (集計のため単一item無し)" if r["answer_kept"] is None else str(r["answer_kept"])
        L.append(f"- **answer_kept**: {kept}")
        L.append("")
        L.append("| context | 回答 | 判定 |")
        L.append("|---|---|:---:|")
        L.append(f"| 原本 (full, {r['original_tokens']}tok) | {r['answer_full']} | "
                 f"{yn(r['full_correct'])} |")
        L.append(f"| 圧縮 (compressed, {r['compressed_tokens']}tok) | {r['answer_compressed']} | "
                 f"{yn(r['compressed_correct'])} |")
        L.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


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
