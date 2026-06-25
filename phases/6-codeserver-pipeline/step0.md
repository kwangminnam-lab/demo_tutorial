# Step 0: config-data-root

## 읽어야 할 파일

먼저 아래 파일을 읽고 아키텍처·계약·규약을 파악하라:

- `docs/ARCHITECTURE.md`
- `docs/ADR.md`
- `docs/CONVENTIONS.md`
- `src/kms/config/settings.py` (Settings 모델 — 모든 설정 필드·env 매핑)
- `src/kms/api/v1/ingest.py` (특히 65행 `_DATA_ROOT = Path("data")`, 126행 `target_dir`, 129행 `write_bytes`)
- `src/kms/api/v1/files.py` (특히 38행 `_DATA_ROOT = Path("data")`)

## 의존 / 노출

- **의존**: `src/kms/config/settings.py`의 `Settings`(pydantic-settings BaseSettings).
- **노출**: `Settings`에 신규 필드 `data_root`(업로드 원본 저장 루트)와 `export_root`(파싱/추출 산출물 .jsonl 내보내기 루트). `ingest.py`·`files.py`가 하드코딩 대신 이 설정을 읽도록 변경.

## 작업

이후 step들이 "code-server PVC 폴더로 적재"(F3/F5)를 구현하려면 저장 경로가 **설정 가능**해야 한다. 현재 `Path("data")`가 두 곳에 하드코딩돼 있어 막혀 있다. 이 step은 경로를 설정화하되 **동작은 바꾸지 않는다**(기본값 = 현행 `data`).

1. `Settings`(`src/kms/config/settings.py`)에 필드 2개 추가:
   - `data_root: str = "data"` — 업로드 원본 저장 루트(env `DOCUX_DATA_ROOT`). 기존 동작 보존 위해 기본값 `"data"`.
   - `export_root: str = "data/export"` — 파싱/추출 산출물(.jsonl) 내보내기 루트(env `DOCUX_EXPORT_ROOT`). 클러스터에선 code-server 공유 PVC 마운트 경로로 오버라이드(예: `/home/coder/workspace/docux-export`). 기본값은 로컬에서 무해한 하위 디렉토리.
   - 다른 설정 필드들의 env 별칭/패턴(대문자 env명, `Field`/alias)을 그대로 따른다. 이 파일에 이미 쓰인 컨벤션을 복제하라.
2. `src/kms/api/v1/ingest.py`: 모듈 상수 `_DATA_ROOT = Path("data")`를 제거하고, 업로드 저장 경로를 `settings.data_root` 기준으로 해석하도록 변경한다. settings 접근은 이 파일이 이미 settings를 얻는 방식(전역 `get_settings()`/주입 등 기존 패턴)을 따른다. 새 패턴을 도입하지 마라.
3. `src/kms/api/v1/files.py`: 같은 방식으로 `_DATA_ROOT`를 `settings.data_root` 기준으로 교체. ingest와 files가 **동일 루트**를 가리켜야 한다(현재도 둘 다 `"data"`).
4. 경로는 `Path(settings.data_root)`로 감싸 사용. 경로 traversal 방어(기존 `_safe_suffix`/소스 검증 등) 로직은 유지.

핵심 규칙:
- 레이어 경계: 설정 읽기는 config 레이어. domain 모델은 건드리지 마라.
- 조용한 실패 금지: 디렉토리 미존재 시 기존 동작(생성 or 에러)을 그대로 보존. 새 except-pass 추가 금지.
- 하위 호환: 기본값을 현행과 동일(`data`)하게 둬 **breaking 아님**. env 미설정 시 동작 불변.

## Acceptance Criteria

```bash
cd da_h
.venv/bin/ruff check src && .venv/bin/mypy src
.venv/bin/python -m pytest -q src/tests/unit/test_config.py
.venv/bin/python -m pytest -q -m "not integration"
```

## 검증 절차

1. 위 AC 커맨드 실행.
2. 체크리스트:
   - `grep -rn 'Path("data")' src/kms/api` 결과가 비어야 한다(하드코딩 잔존 없음).
   - env 미설정 시 `Settings().data_root == "data"`.
   - 기존 ingest/files 테스트가 깨지지 않는다.
   - 시크릿을 코드/커밋에 넣지 않았는가?
3. 결과를 `phases/6-codeserver-pipeline/step0.result.json`에 유효한 JSON 객체 하나로만 기록:
   - 성공 → `{"status": "completed", "summary": "Settings에 data_root/export_root 추가(env DOCUX_DATA_ROOT/DOCUX_EXPORT_ROOT, 기본 data·data/export). ingest.py·files.py 하드코딩 Path(\"data\") 제거→settings.data_root. 동작 불변."}`
   - 실패 → `{"status": "error", "error_message": "..."}`
   - 차단 → `{"status": "blocked", "blocked_reason": "..."}`

## 금지사항

- 기본값을 `data` 외 값으로 바꾸지 마라. 이유: 로컬/기존 배포 동작이 깨진다(breaking).
- 저장 경로 외의 로직(파싱·추출·검색)을 건드리지 마라. 이유: 이 step은 config 한정.
- `phases/6-codeserver-pipeline/index.json`을 편집하지 마라. 이유: 하네스가 전유. 결과는 `step0.result.json`에만.
- 기존 테스트를 깨뜨리지 마라.
