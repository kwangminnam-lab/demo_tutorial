# Step 4: jsonl-output

## 읽어야 할 파일

먼저 아래를 읽고 파싱/추출 응답 계약과 구조 모델을 파악하라:

- `docs/ARCHITECTURE.md`, `docs/ADR.md`, `docs/CONVENTIONS.md`
- `src/kms/adapters/ingestion/structure.py` (step 3: `Section`, `build_sections`, `markdown_to_text`)
- `src/kms/api/v1/parse.py` (`ParseResponse{filename, doc_type, html, json_data, page_previews, page_map}`, `_ir_to_dict`)
- `src/kms/api/v1/extract.py` (`ExtractResponse`, `POST /run`, `ExtractionResult`, `ExtractionField`)
- `src/kms/adapters/ingestion/ir.py` (`MarkdownDoc`, `SlideDeck`, `Workbook`)

## 의존 / 노출

- **의존**: step 3의 `build_sections(markdown, page_map)`·`markdown_to_text`·`Section`.
- **노출**: 파싱/추출 결과를 **JSONL(섹션당 1줄)** 문자열로 만드는 직렬화 함수 + 다운로드 라우트.
  - `to_parse_jsonl(ir, filename, page_map) -> str`: 섹션 1개 = JSON 1줄.
  - `to_extract_jsonl(result) -> str`: 추출 결과의 직렬화(아래 단위 규칙 참고).
  - 라우트: 파싱 .jsonl 다운로드, 추출 .jsonl 다운로드.

## 작업

사용자 결정: **.jsonl, 섹션(heading)당 1줄**. 기존 `.json`(응답 내 `json_data`)에 더해 JSONL 직렬화를 추가한다.

1. `src/kms/adapters/ingestion/jsonl.py`(신규, 또는 api 보조 모듈) 순수 함수:
   - `to_parse_jsonl(ir: MarkdownDoc | SlideDeck | Workbook, filename: str, page_map) -> str`:
     - `MarkdownDoc`: `build_sections(ir.markdown, page_map)`로 섹션 추출 → **섹션마다 한 줄** JSON.
     - 각 줄(레코드) 스키마(키 고정):
       `{"filename": str, "doc_type": str, "section_index": int, "level": int, "title": str, "path": list[str], "page": int|null, "text": str}`
       (`text`는 plain text — markdown→text 적용된 섹션 본문.)
     - `SlideDeck`: 슬라이드 1장 = 1줄(`level`=1, `title`=슬라이드 번호/제목, `text`=슬라이드 텍스트, `page`=번호).
     - `Workbook`: 시트 단위 등 자연스러운 1줄 단위(구현 재량, 빈 결과 금지).
     - 출력은 줄마다 `json.dumps(..., ensure_ascii=False)` + `\n`. 마지막 줄도 `\n` 종료(표준 JSONL).
   - `to_extract_jsonl(result: ExtractionResult) -> str`: 추출 필드 1개 = 1줄
     `{"doc_id": str, "schema_id": int, "key": str, "value": ..., "page": int|null, "bbox": [...]|null, "confidence": float|null, "needs_review": bool}`.
     (추출은 섹션 개념이 아니라 필드 단위가 자연 단위 — 사용자 4번 선택의 파싱=섹션, 추출=필드.)
2. 다운로드 라우트(`parse.py`·`extract.py` 또는 신규 라우터):
   - 파싱: 업로드 파일을 파싱해 `to_parse_jsonl` 결과를 `application/x-ndjson`(또는 `text/plain`)으로, `Content-Disposition: attachment; filename="<원본>.jsonl"`로 반환.
   - 추출: 이미 추출된 결과(또는 `/run` 직후)를 jsonl로 반환.
   - 기존 `ParseResponse.json_data`(미리보기용 .json)는 **유지**(비파괴). jsonl은 별도 추가.
3. `_ir_to_dict`는 그대로 두고, jsonl 경로를 신설(미리보기는 json, 다운로드/내보내기는 jsonl).

핵심 규칙:
- 레이어 경계: 직렬화 순수 함수는 adapters, HTTP 반환은 api. 파일 쓰기는 하지 마라(step 5가 export_root에 영속).
- 조용한 실패 금지: 빈 문서도 최소 1줄(단일 섹션) 생성. 직렬화 실패 시 명시적 에러.
- 하위 호환: 기존 `/parse/upload` 응답 스키마를 깨지 마라(jsonl은 신규 엔드포인트/필드로 추가). breaking 아님.
- 한국어 보존: `ensure_ascii=False`.

## Acceptance Criteria

```bash
cd da_h
.venv/bin/ruff check src && .venv/bin/mypy src
.venv/bin/python -m pytest -q src/tests/unit/test_jsonl.py
.venv/bin/python -m pytest -q -m "not integration"
```

(테스트 `src/tests/unit/test_jsonl.py` 신규.)

## 검증 절차

1. 위 AC 실행.
2. 체크리스트:
   - 파싱 jsonl: 줄 수 == 섹션 수, 각 줄이 유효 JSON, 키 스키마 고정, `text`가 plain text.
   - `json.loads`로 각 줄 파싱 가능(NDJSON 유효성).
   - 추출 jsonl: 필드당 1줄.
   - 기존 `/parse/upload` 응답 무변(회귀 테스트).
   - 한글이 escape되지 않고 보존(`ensure_ascii=False`).
3. 결과를 `phases/6-codeserver-pipeline/step4.result.json`에 기록:
   - 성공 → `{"status": "completed", "summary": "adapters/ingestion/jsonl.py: to_parse_jsonl(섹션당 1줄: filename,doc_type,section_index,level,title,path,page,text)·to_extract_jsonl(필드당 1줄). parse/extract에 .jsonl 다운로드 라우트 추가(application/x-ndjson, 기존 json 응답 유지). test_jsonl.py 추가."}`
   - 실패/차단 → 해당 스키마.

## 금지사항

- 파일시스템에 쓰지 마라. 이유: 영속(export_root)은 step 5.
- 기존 `ParseResponse`/`ExtractResponse` 필드를 제거·개명하지 마라. 이유: 프론트·테스트 breaking.
- 섹션 단위를 페이지/문서 단위로 바꾸지 마라. 이유: 사용자 결정=섹션당 1줄.
- `phases/6-codeserver-pipeline/index.json`을 편집하지 마라. 결과는 `step4.result.json`에만.
