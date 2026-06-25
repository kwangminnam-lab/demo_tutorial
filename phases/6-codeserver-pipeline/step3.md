# Step 3: doc-structure

## 읽어야 할 파일

먼저 아래를 읽고 IR(중간표현)과 파싱 계약을 파악하라:

- `docs/ARCHITECTURE.md`, `docs/ADR.md`, `docs/CONVENTIONS.md`
- `src/kms/adapters/ingestion/ir.py` (`MarkdownDoc{markdown, page_map, image_blobs}`, `SlideDeck{slides}`, `Workbook`)
- `src/kms/adapters/ingestion/extract/pdf_digital.py` (`DoclingDigitalParser` — Docling이 IR을 만드는 곳)
- `src/kms/api/v1/parse.py` (`_ir_to_dict`, `ParseResponse`)
- `src/kms/adapters/ingestion/chunk/markdown_chunker.py` (마크다운 heading 파싱 기존 패턴 — 재사용 가능)

## 의존 / 노출

- **의존**: `ir.py`의 `MarkdownDoc.markdown`(마크다운 문자열, `#`/`##` heading 포함).
- **노출**:
  1. 문서 구조 모델 `Section`(adapters/ingestion 레이어): `level: int`(heading 깊이 1..N), `title: str`, `text: str`(해당 섹션 본문 plain text), `page: int | None`(시작 페이지, page_map으로 해석), `path: list[str]`(상위 heading 제목 경로, 선택).
  2. `markdown` 문자열 → `list[Section]` 변환 함수 `build_sections(markdown: str, page_map: list[tuple[int,int]]) -> list[Section]`.
  3. `markdown` → plain text 변환 유틸 `markdown_to_text(markdown: str) -> str`(heading 기호·강조·링크·이미지 마커 제거, 가독 텍스트).

## 작업

F6: "파싱 결과가 문서 글의 구조를 반영" + "markdown→text". IR의 `markdown`은 heading 계층이 암묵적(`##`)이라, 이를 **명시 섹션 트리**로 뽑고 plain text도 제공한다. 이 step은 **순수 변환 로직만** 만든다(직렬화·파일쓰기는 step 4/5).

1. `src/kms/adapters/ingestion/structure.py`(신규) 모듈에:
   - `@dataclass(frozen=True) Section`: 위 필드. domain이 아니라 adapters/ingestion 레이어(파싱 산출물 표현).
   - `build_sections(markdown, page_map) -> list[Section]`:
     - 마크다운을 줄 단위로 훑어 `#`~`######` heading을 경계로 섹션 분할.
     - 각 섹션 `text`는 그 heading 다음부터 다음 heading 전까지의 본문을 `markdown_to_text`로 정제.
     - `page`는 `page_map`(문자 오프셋→페이지)으로 섹션 시작 오프셋을 해석. page_map이 비면 `None`.
     - heading이 하나도 없으면 전체를 단일 `Section(level=0, title=파일/문서, text=전체텍스트)`로 반환(빈 결과 금지).
     - `path`는 상위 heading 제목들의 누적(예: ["1장","1.1절"]). 구현 재량이나 level 일관성 유지.
   - `markdown_to_text(markdown) -> str`: heading 기호/리스트 마커/강조/링크/`[IMAGE ...]` 마커 제거. 기존 `parse.py`의 `_IMAGE_MARKER_RE` 패턴을 참고(중복 정의 말고 공용화 고려하되, 범위 넘으면 동일 정규식 복제 허용).
2. heading 파싱은 `markdown_chunker.py`에 유사 로직이 있으면 **재사용/추출**한다(중복 최소화). 단 chunker의 공개 동작을 깨지 마라 — 공용 헬퍼로 빼낼 때 chunker 테스트가 그대로 통과해야 한다.
3. 이 step은 `parse.py`/`ir.py`의 **공개 응답 형태를 바꾸지 않는다**(step 4가 직렬화에서 사용). `ir.py`에 `Section`을 넣을지 새 모듈에 둘지는 레이어상 `structure.py` 신설을 권장(IR은 원자료, 구조는 파생).

핵심 규칙:
- 레이어 경계: 순수 함수(외부 I/O·네트워크·DB 없음). domain import 금지(adapters 내부 자족).
- 조용한 실패 금지: 파싱 불가 입력도 빈 결과가 아니라 단일 섹션으로 graceful 반환. 예외를 삼키지 마라.
- 결정론: 같은 입력 → 같은 섹션 리스트(테스트 가능).

## Acceptance Criteria

```bash
cd da_h
.venv/bin/ruff check src && .venv/bin/mypy src
.venv/bin/python -m pytest -q src/tests/unit/test_structure.py
.venv/bin/python -m pytest -q -m "not integration"
```

(테스트 파일 `src/tests/unit/test_structure.py`를 신규 작성하라.)

## 검증 절차

1. 위 AC 실행.
2. 체크리스트:
   - heading 다단계(`#`/`##`/`###`) 마크다운 → 올바른 level·title·계층(path).
   - heading 없는 평문 → 단일 섹션(빈 리스트 아님).
   - `page_map` 주어지면 섹션 `page`가 올바른 페이지로 매핑, 비면 `None`.
   - `markdown_to_text`가 heading 기호·`[IMAGE ...]` 마커·강조를 제거.
   - 순수 함수인가(I/O 없음)? domain import 없는가?
3. 결과를 `phases/6-codeserver-pipeline/step3.result.json`에 기록:
   - 성공 → `{"status": "completed", "summary": "adapters/ingestion/structure.py 신설: Section(level,title,text,page,path) + build_sections(markdown,page_map)->list[Section] + markdown_to_text(markdown)->str. heading 계층 추출·평문화. test_structure.py 추가. 순수함수."}`
   - 실패/차단 → 해당 스키마.

## 금지사항

- 파일을 쓰거나 네트워크를 타지 마라. 이유: 이 step은 순수 변환 로직 한정(I/O는 step 4/5).
- `parse.py`/`extract.py`의 응답 스키마를 바꾸지 마라. 이유: step 4가 담당.
- `markdown_chunker`의 공개 동작/테스트를 깨지 마라. 이유: 적재 파이프라인 회귀.
- `phases/6-codeserver-pipeline/index.json`을 편집하지 마라. 결과는 `step3.result.json`에만.
