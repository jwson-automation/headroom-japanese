# 아침 요약 (overnight 작업 결과)

밤새 자율로 돌린 결과. **원칙대로 전부 headroom 기반 이식, 재발명 없음. 유료 LLM API는
밤새 안 씀(키 보호 + 비용)** — 무료 작업(이식·테스트·문서·deterministic 벤치)만.

## 한 것 (전부 커밋·푸시됨)
| ver | 내용 | 출처 |
|---|---|---|
| 0.7.0 | 관련성 = headroom 등급 점수 이식 (내 IDF 발명 제거) | `ccr/context_tracker._calculate_relevance` |
| 0.8.0 | **proactive expansion** 이식 (묻기 전 미리 펼침, workspace 격리) | `ccr/context_tracker.py` |
| 0.9.0 | **log/search/diff 압축기** (JSON 외 타입) + 라우터 순서 버그 수정 | content-type handlers |
| 0.10.0 | **무손실 컬럼 압축** (행 0개 버림, 46% 절감) | `compaction/compactor.rs` |
| docs | ARCHITECTURE / CHANGELOG / LIMITATIONS | — |

## 현재 상태
- **테스트 36/36 통과** (tokenizer, crusher, context_tracker, text_compress, compaction, edge_cases)
- **deterministic 벤치**: 19종 mean 89% 절감, answer_kept 100% (16 단일정답 케이스)
- 모듈↔headroom 출처 매핑은 `ARCHITECTURE.md` 참고

## 네가 아침에 할 것
1. **그 API 키 폐기/재발급** (밤새 안 썼지만 채팅에 노출돼 있음): console.anthropic.com/settings/keys
2. 원하면 **LLM 벤치 1회** 돌려 retention 재확인 (새 키로):
   ```
   $env:ANTHROPIC_API_KEY="새키"; python -m benchmarks.run_eval
   ```
   → `benchmark.md` + `benchmarks/history/<ver>_<time>.md` 갱신됨
3. 새로 들어온 무손실/proactive expansion을 LLM 벤치에 태우고 싶으면 데이터셋/하네스 확장

## 남은 후보 (다음 세션)
- 멀티턴 LLM 벤치: proactive expansion이 실제로 retrieve를 없애는지 수치 검증
- 무손실 vs lossy+retrieve 절감/품질 trade 비교 리포트
- code(AST) 압축기 (headroom엔 있음, 우리 미구현)
- (선택) 프록시 모드 — 라이브러리 방향이라 보류 중

## 밤새 추가 진행 (iter 2–7, 전부 무료·푸시됨)
| iter | 내용 | ver |
|---|---|---|
| 2 | 코드 압축기 (임포트/시그니처 유지, 본문 `...`) + 라우터 `code` 탐지 | 0.11.0 |
| 3 | 적대적 데이터셋 4개 → **실버그 발견·수정**(관련성이 키 이름까지 매칭→압축무효; 값만 매칭으로) | 0.12.0 |
| 4 | `keep_top_k`(2등 랭킹 갭 해결, 기본 1=현행) | 0.13.0 |
| 5 | CodeCompressor `docstring_mode`(remove/first_line/full) | 0.14.0 |
| 6 | 2글자 한자 부분매칭 시도→**회귀라 되돌림**(정직 기록); `LOSSLESS_VS_LOSSY.md` | — |
| 7 | README 현행화(콘텐츠 타입·모드·멀티턴 반영) | — |

**최종 상태**: 테스트 **44/44**, deterministic 벤치 23종 mean 88% 절감, answer_kept 95%
(남은 1개 = second_highest 랭킹 갭, `keep_top_k=2`로 해결 가능 — 의도된 기본값 트레이드오프).
새 문서: `ARCHITECTURE.md` / `CHANGELOG.md` / `LIMITATIONS.md` / `LOSSLESS_VS_LOSSY.md`.

루프는 iter 7에서 **자가 종료**(저위험 무료 개선거리 소진; 남은 큰 항목=멀티배열 압축은
LLM 벤치 검증이 필요해 네가 깨어있을 때 하는 게 안전).

## 한계 (정직하게, `LIMITATIONS.md`)
- 집계는 retrieve 필요(lossy 본질). 모델이 원본 줘도 가끔 카운트/합 틀림.
- 과잉 retrieve는 LLM 노이즈 → 결정적 해법은 proactive expansion(이식 완료).
- 룰베이스 토크나이저 한계(가나 글루) → `pip install '.[accurate]'`로 fugashi 교체 가능.
