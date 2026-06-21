# headroom-japanese

[English](README.md) · [日本語](README.ja.md) · **한국어**

[headroom](https://github.com/chopratejas/headroom)의 **코어 아이디어**(ContentRouter + SmartCrusher + CCR)를
형태소 분석기 없이 **룰베이스로** 처음부터 재구현한 **일본어** 컨텍스트 압축기.
데이터·질문 모두 일본어 기준. 버전업·벤치마킹은 직접 한다는 전제로 만들었다.

> headroom 본체는 핵심이 Rust로 포팅돼 있고 표면적이 크다. 여기서는 최소 코어만
> **순수 Python + 표준 라이브러리만으로** 들고 와서 일본어에 맞게 고쳤다.

## 원리 (LLM에 보내기 전 배열을 줄이는 6단계)

| # | 규칙 | 방식 | 일본어 대응 |
|---|------|------|-------------|
| 1 | 중복 버리기 | 내용 MD5 해시 같으면 1개만 | 언어 무관 |
| 2 | **에러 보존** | 키워드 포함 검사 | `lexicon_ja.py` 일본어 키워드 |
| 3 | 숫자 이상치 | 필드별 z-score > 2σ | 언어 무관 |
| 4 | 구조 이상 | 희귀 필드 가진 항목 | 언어 무관 |
| 5 | **질문 관련** | 키워드 겹침 | 룰베이스 일본어 토크나이저 |
| 6 | 처음/끝 + 채우기 | 위치 + 균등 샘플 | 언어 무관 |

치명적 항목(2~5)은 **예산을 넘겨서라도 다 남긴다**(quality guarantee).
버린 원본은 CCR 캐시에 보관 → `retrieve(key, query)`로 되찾는다.

## 일본어 토크나이저 (형태소 분석기 없음)

기능어(조사·접속사·동사어미·구두점)를 **구분자**로 써서 그 사이 내용어를 키워드로 뽑는다.

```
新しいパソコンが欲しい、そして安いキーボードも探している
→ keyword: 新しいパソコン / 安いキーボード / 探
  particle: が / も   ending: 欲しい / している   conj: そして
```

한계: 조사가 단어 안 글자로 박히면 오분리(예: `長い`의 が).
한자 명사 위주면 위험 낮음. 정확도 필요하면 `pip install '.[accurate]'` 후 `fugashi`로 교체.

## 설치 / 실행

```bash
cd headroom-japanese
pip install -e .              # 코어만 (의존성 0)
pip install -e '.[accurate]'  # tiktoken(정확 토큰) + fugashi(형태소)

python examples/demo.py       # 500건 -> 압축 데모
pytest                        # 테스트
```

## 사용

```python
from headroom_ja import compress, retrieve

r = compress(tool_output_json, query="拒否された注文はある？")
print(r)               # [json] 18000 -> 1200 tok (93% saved, 15 kept / 485 dropped)
send_to_llm(r.text)

# LLM이 더 봐야 하면
more = retrieve(r.cache_key, query="拒否")
```

## 튜닝 지점

- `headroom_ja/lexicon_ja.py` — 조사/접속사/어미/에러 키워드 리스트.
  **여기를 데이터에 맞게 고친다.**
- `CrusherConfig` — `max_items`, `variance_threshold`, `first/last_fraction` 등.

## 벤치마킹

`CompressResult`만 로깅하면 버전 비교 가능:
`(content_type, original_tokens, compressed_tokens, kept, dropped, ratio)`.
+ `retrieve` 호출 빈도(=과압축 신호)를 같이 보면 절감률 vs 품질 트레이드오프가 보인다.

## 라이선스

Apache-2.0 (원본 headroom과 동일).
