# Overnight autonomous plan (headroom-grounded)

Running unattended. **Rule: port from headroom, don't reinvent. No paid LLM API
overnight** — only free work (code, tests, docs, deterministic `--no-llm` bench).
The owner runs the LLM benchmark in the morning with their own key.

Source of truth for each port: `chopratejas/headroom`.

## Phases (check off as done)

- [x] **A. Content-type compressors** — DONE. log/search/diff line compressors
      (`text_compress.py`), routed via `compress()`; fixed router ordering
      (timestamp vs file:line). Tests 26/26.
      <!-- original scope below -->
- [ ] ~~A. Content-type compressors~~ — we only compress JSON; headroom routes
      log / search / diff / text too. Port line-based compressors:
      - LogCompressor: template-dedup, keep errors/timestamps, "[N similar omitted]"
      - SearchCompressor: `file:line:content`, dedup by file, keep matches
      - DiffCompressor: keep hunk headers + changed lines, trim context
      Wire into `compress()` via the router. Tests + bench datasets.
- [x] **B. Lossless columnar compaction** — DONE. `compaction.py`: columns+rows
      form, lossless; `compress(config=CrusherConfig(lossless_first=True))` runs it
      before lossy and accepts at ≥15% savings (default off). 46% on uniform data,
      0 rows dropped. Tests 30/30.
- [x] **C. Multi-turn proactive-expansion** — covered by `tests/test_context_tracker.py`
      (pre-expansion returns the dropped relevant item, workspace isolation,
      irrelevant query no-op). Deterministic, no LLM.
- [x] **D. Docs** — ARCHITECTURE.md (module↔headroom map), CHANGELOG.md (0.1→0.10),
      LIMITATIONS.md.
- [x] **E. Guardrails / edge cases** — `tests/test_edge_cases.py` (empty/whitespace,
      empty array, scalar arrays, malformed JSON→text, mixed scalar/dict, unicode
      roundtrip). 36/36 tests. (JSON detection uses real json.loads, so #553-style
      bracket-miscounting doesn't apply; bench file IO already UTF-8.)
- [x] **F. Polish** — CHANGELOG done, MORNING.md written, final --no-llm bench.

## Progress log
- iter 1: Phase A done — log/search/diff compressors + router-order fix. 26/26 tests.
- iter 1: Phase B done — lossless columnar compaction (46% saved, 0 dropped). 30/30 tests.
- iter 1: Phases C/D/E/F done — edge tests (36/36), ARCHITECTURE/CHANGELOG/LIMITATIONS, MORNING.md.
  All planned phases complete in one session (cheap cached context); a final review
  wakeup is scheduled to re-run the full suite and catch anything.
- iter 2: code compressor (headroom CodeCompressor concept, line-based) — keep
  imports/signatures/class, drop function bodies -> `...`; router detects `code`.
  v0.11.0. 40/40 tests.
- iter 3: +4 adversarial datasets (second_highest, nested_two_arrays,
  item_nested_field, median). FOUND + FIXED a real bug: relevance matched JSON
  KEY NAMES (query 'tier' kept every item -> 0% compression); now matches VALUES
  only (item_nested_field 0%->92%). Documented ranking-beyond-extremes gap
  (second_highest). v0.12.0. 40/40 tests, bench answer_kept 95% (1 honest gap).
- iter 4: keep_top_k config (default 1=min/max; 2 keeps the runner-up) to close
  the second-highest ranking gap on demand. v0.13.0. 41/41 tests.
- iter 5: CodeCompressor docstring_mode (remove default / first_line / full),
  like headroom DocstringMode; keeps a one-line function-purpose hint. v0.14.0. 44/44 tests.
- iter 6: investigated 2-char kanji partial-match (東京⊂東京都); lowering the CJK
  substring threshold REVERTED (re-introduced generic-word pollution) — documented
  as a real tradeoff. Added LOSSLESS_VS_LOSSY.md (lossy 92%/retrieve vs lossless 46%/none).
- iter 7: README updated to current capabilities (content types/modes/multi-turn).
  STOPPING — low-risk free improvements exhausted; remaining big item (multi-array
  envelopes) needs LLM-bench verification when the owner is awake. Final: 44/44 tests.
