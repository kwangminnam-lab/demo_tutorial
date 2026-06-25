# Step 5: export-artifacts

## 읽어야 할 파일

먼저 아래를 읽고 저장 경로 설정·업로드·직렬화 계약을 파악하라:

- `docs/ARCHITECTURE.md`, `docs/ADR.md`, `docs/CONVENTIONS.md`
- `src/kms/config/settings.py` (step 0: `data_root`, `export_root`)
- `src/kms/api/v1/ingest.py` (step 0: `settings.data_root`로 업로드 저장)
- `src/kms/api/v1/parse.py`, `src/kms/api/v1/extract.py` (step 4: jsonl 직렬화/다운로드)
- `src/kms/adapters/ingestion/jsonl.py` (step 4: `to_parse_jsonl`, `to_extract_jsonl`)

## 의존 / 노출

- **의존**: step 0 `settings.export_root`·`settings.data_root`, step 4 jsonl 직렬화 함수.
- **노출**: 파싱/추출 시 산출 .jsonl(및 업로드 원본)을 `export_root`(클러스터=code-server 공유 PVC 마운트) 아래에 **영속**하는 헬퍼 + 그 호출 배선.

## 작업

F3·F5: "문서 추가/파싱/OCR 산출물이 code-server 노드 폴더(공유 PVC)로 들어가도록". 경로는 step 0에서 설정화됨. 이 step은 **실제 파일 쓰기**를 추가한다(헬름 PVC 마운트는 step 8).

1. `src/kms/adapters/storage/export.py`(신규) 또는 적절한 adapters 위치에:
   - `write_export(rel_dir: str, filename: str, content: str | bytes, export_root: str) -> Path`:
     - `Path(export_root)/rel_dir`를 생성(`mkdir(parents=True, exist_ok=True)`)하고 파일을 쓴다. 절대경로/`..` 주입 방어(filename은 basename만).
     - 반환: 쓰여진 경로.
   - 원자적 쓰기 권장(임시파일 후 rename) — 동시 접근(RWX PVC) 안전.
2. 배선:
   - **업로드(F3)**: `ingest.py` 업로드 시 원본을 `data_root`에 저장하는 기존 동작 유지. 추가로 사용자가 "code-server 폴더로"를 원하므로, 업로드 저장 루트를 `export_root` 하위로 둘지 `data_root`로 둘지는 step 0 기본값에 따른다(클러스터에선 둘 다 PVC로 오버라이드 가능). **중복 저장을 만들지 마라** — 업로드는 한 곳(설정된 루트)에만.
   - **파싱(F5)**: `/parse` 경로(또는 신규 "export" 액션)에서 `to_parse_jsonl` 결과를 `write_export("parse", "<원본>.jsonl", jsonl, settings.export_root)`로 저장. 어디에 저장됐는지 응답에 경로(또는 상대경로) 한 줄 포함.
   - **추출(F5)**: `/run`(또는 export 액션)에서 `to_extract_jsonl` 결과를 `write_export("extract", "<doc_id>.jsonl", ...)`로 저장.
   - 다운로드(step 4)와 별개로, **서버측 영속**을 추가하는 것이다. 다운로드는 그대로 둔다.
3. export 실패 처리: PVC 미마운트/권한 오류 등은 **조용히 무시하지 마라**. 다만 파싱/추출의 핵심 응답(미리보기·필드)은 export 실패와 분리해 사용자에게 결과를 돌려주되, export 실패는 로그(warning) + 응답에 `export_error` 플래그/메시지로 노출한다(조용한 실패 금지, 핵심 기능은 graceful).

## Acceptance Criteria

```bash
cd da_h
.venv/bin/ruff check src && .venv/bin/mypy src
.venv/bin/python -m pytest -q src/tests/unit/test_export.py
.venv/bin/python -m pytest -q -m "not integration"
```

(테스트 `src/tests/unit/test_export.py` 신규 — `tmp_path` fixture로 export_root를 임시 디렉토리로 두고 검증. 실제 PVC 불요.)

## 검증 절차

1. 위 AC 실행.
2. 체크리스트:
   - `write_export`가 `export_root/<rel_dir>/<filename>`에 정확히 쓰고 경로 반환.
   - `..`/절대경로 filename 주입이 차단됨.
   - 파싱/추출 후 jsonl이 export_root에 생성됨(tmp_path 테스트).
   - export 실패가 조용히 삼켜지지 않고 로그+플래그로 노출.
   - 디렉토리 자동 생성(`parents=True`).
3. 결과를 `phases/6-codeserver-pipeline/step5.result.json`에 기록:
   - 성공 → `{"status": "completed", "summary": "adapters/storage/export.py: write_export(rel_dir,filename,content,export_root)->Path(원자적쓰기·traversal방어). parse는 export_root/parse/<name>.jsonl, extract는 export_root/extract/<doc_id>.jsonl로 영속. export 실패는 warning+응답 플래그(핵심응답 graceful). test_export.py 추가."}`
   - 실패/차단 → 해당 스키마.

## 금지사항

- export_root를 코드에 하드코딩하지 마라. 이유: step 0 설정·step 8 PVC 마운트로 주입. 반드시 `settings.export_root` 사용.
- 같은 업로드를 두 루트에 중복 저장하지 마라. 이유: PVC 용량·혼란.
- export 실패를 `except: pass`로 삼키지 마라. 이유: 조용한 실패 금지 — 사용자가 적재 실패를 알아야 한다.
- 헬름/매니페스트를 건드리지 마라. 이유: step 8.
- `phases/6-codeserver-pipeline/index.json`을 편집하지 마라. 결과는 `step5.result.json`에만.
