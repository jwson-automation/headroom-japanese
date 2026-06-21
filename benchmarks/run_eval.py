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
    python -m benchmarks.run_eval               # full (LLM) run
    python -m benchmarks.run_eval --no-llm      # deterministic only, no spend
"""

from __future__ import annotations

import argparse
import json
import sys

from headroom_ja import compress
from headroom_ja.tokens import BACKEND

from . import datasets


def _answer_kept(text: str, answer_ids: list[int]) -> bool:
    """True if every answer-bearing item (by unique id) survived compression."""
    return all(f'"id": {i}' in text for i in answer_ids)


def run(use_llm: bool, out_path: str | None):
    samples = datasets.build()
    rows = []

    for name, data, question, gold, answer_ids in samples:
        content = json.dumps(data, ensure_ascii=False)
        r = compress(content, query=question)
        kept = _answer_kept(r.text, answer_ids)

        row = {
            "dataset": name,
            "token_backend": BACKEND,
            "n_items": len(data),
            "original_tokens": r.original_tokens,
            "compressed_tokens": r.compressed_tokens,
            "ratio": round(r.ratio, 4),
            "kept": r.kept,
            "dropped": r.dropped,
            "answer_ids": answer_ids,
            "answer_kept": kept,        # deterministic retention signal
        }

        if use_llm:
            from . import models
            a_full = models.answer(question, content)
            a_comp = models.answer(question, r.text)
            j_full = models.judge(question, gold, a_full)
            j_comp = models.judge(question, gold, a_comp)
            row.update({
                "gold": gold,
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

    return rows


def _print_row(row, use_llm):
    base = (f"[{row['dataset']:<18}] {row['original_tokens']:>6} -> "
            f"{row['compressed_tokens']:>5} tok  ({row['ratio']:>5.0%})  "
            f"answer_kept={row['answer_kept']}")
    if use_llm:
        base += (f"  full={row['full_correct']}  "
                 f"compressed={row['compressed_correct']}")
    print(base)


def _print_summary(rows, use_llm):
    n = len(rows)
    mean_ratio = sum(r["ratio"] for r in rows) / n
    kept_rate = sum(r["answer_kept"] for r in rows) / n
    print("\n" + "=" * 60)
    print(f"samples           : {n}")
    print(f"mean savings      : {mean_ratio:.0%}")
    print(f"answer_kept rate  : {kept_rate:.0%}  (deterministic)")
    if use_llm:
        answerable = [r for r in rows if r["full_correct"]]
        if answerable:
            retention = sum(r["compressed_correct"] for r in answerable) / len(answerable)
            print(f"retention         : {retention:.0%}  "
                  f"(of {len(answerable)} the model got right on full context)")
        else:
            print("retention         : n/a (model got none right even on full context)")
    print("=" * 60)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-llm", action="store_true",
                    help="deterministic only (answer_kept + savings), no API spend")
    ap.add_argument("--out", default="benchmarks/results/latest.jsonl",
                    help="JSONL output path (set empty to skip)")
    args = ap.parse_args()

    if not args.no_llm:
        import os
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("ANTHROPIC_API_KEY not set. Either export it (a key you can "
                  "rotate) or run with --no-llm.", file=sys.stderr)
            sys.exit(2)
        import os as _os
        _os.makedirs("benchmarks/results", exist_ok=True)

    run(use_llm=not args.no_llm, out_path=args.out or None)


if __name__ == "__main__":
    main()
