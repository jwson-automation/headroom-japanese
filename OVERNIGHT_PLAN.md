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
- [ ] **B. Lossless columnar compaction** — headroom tries lossless CSV/KV BEFORE
      lossy row-drop (compaction/compactor.rs). Port: array-of-objects with shared
      core keys → header + rows; accept if it saves ≥15% (lossless_min_savings_ratio).
      Run before `crush_array`. Tests.
- [ ] **C. Multi-turn proactive-expansion harness** — deterministic test that a
      turn-5 query pre-expands the right dropped blob and avoids retrieve. (Free:
      no LLM; assert on the expansion block + retrieve-avoidance logic.)
- [ ] **D. Docs** — ARCHITECTURE.md mapping every module to its headroom source;
      update READMEs (EN/JA/KO) with the new compressors + tracker; LIMITATIONS.md.
- [ ] **E. Guardrails from headroom's history** — string-literal-aware JSON
      detection (#553), structural array counting note (#887), UTF-8/`newline=""`
      file IO, percent-encode non-ASCII (env note). Edge-case tests.
- [ ] **F. Polish** — CHANGELOG.md (v0.2→current), final `--no-llm` bench + history
      snapshot, tidy. Leave a MORNING.md summary for the owner.

## Progress log
- iter 1: Phase A done — log/search/diff compressors + router-order fix. 26/26 tests.
