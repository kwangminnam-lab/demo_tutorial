# Step 1: remove-auth-backend

## 읽어야 할 파일

먼저 아래 파일을 읽고 인증/권한 배선과 계약을 파악하라:

- `docs/ARCHITECTURE.md`, `docs/ADR.md`, `docs/CONVENTIONS.md`
- `src/kms/api/deps.py` (130행 `get_current_user`, 160행 `require_ingest_admin`, 176행 `require_master`)
- `src/kms/api/v1/auth.py` (35행 `POST /auth/login`)
- `src/kms/services/auth_service.py` (`login()`)
- `src/kms/domain/models.py` (`UserContext{user_id, department, access_level: AccessLevel}`, `AccessLevel`)
- `src/kms/api/v1/search.py`, `src/kms/api/v1/extract.py`, `src/kms/api/v1/ingest.py` (이 의존성들을 쓰는 라우트)
- `src/tests/unit/test_auth.py`, `src/tests/unit/test_account_and_login.py`, `src/tests/_auth_tokens.py`, `src/tests/conftest.py`

## 의존 / 노출

- **의존**: `src/kms/api/deps.py`의 인증 의존성, `domain/models.py`의 `UserContext`/`AccessLevel`.
- **노출**: 토큰 없이도 모든 라우트가 동작(데모). `get_current_user`/`require_master`/`require_ingest_admin`가 **고정 all-access 마스터 컨텍스트**를 반환. `/auth/login` 라우트 제거.

## 작업

사용자 결정: **로그인 전체 제거(데모)**. 단, 인증/권한 배선을 통째로 뜯지 말고 **최소 침습**으로: 의존성이 항상 전체접근(마스터) 컨텍스트를 주도록 바꾼다(회귀·되돌리기 비용 최소).

1. `src/kms/api/deps.py`:
   - `get_current_user`를 **토큰 검증 없이** 고정 컨텍스트를 반환하도록 변경: 최고 권한 `AccessLevel`(가장 높은 등급) + 데모용 `user_id`(예: `"demo"`)·`department`(예: `""` 또는 전체). Authorization 헤더가 없어도 401을 내지 않는다.
   - `require_master`·`require_ingest_admin`도 같은 마스터 컨텍스트를 반환(게이팅 무력화). 함수 시그니처(반환형 `UserContext`)는 유지해 호출부 변경 0.
   - `AccessLevel`의 "가장 높은 등급"이 무엇인지는 `domain/models.py`의 enum 정의를 읽고 정확히 고른다(search access 하드필터가 `<=`로 동작하므로 최고 등급이어야 전체가 보인다).
2. `src/kms/api/v1/auth.py`: `POST /auth/login` 라우트를 제거한다(로그인 개념 삭제). 라우터가 비면 `main`/app 등록부에서 라우터 포함도 함께 정리(등록 라인 제거). 단, `auth_service`/계정 모델 자체는 다른 곳에서 참조될 수 있으니 **무참조 확인 후**에만 삭제. 참조가 남으면 라우트만 제거하고 서비스는 보존.
3. search/ingest/extract 라우트: `get_current_user`/`require_*` 의존성 시그니처는 그대로 두므로 **수정 불필요**. access_level 하드필터·부서 가중 내부 로직도 유지(항상 마스터 컨텍스트가 들어가 전체 통과). 내부 필터 로직을 삭제하지 마라(되돌리기 가능성 보존).
4. 테스트:
   - 토큰 만료/401을 검증하던 인증 테스트(`test_auth.py` 등)는 **새 동작(토큰 불요 = 항상 통과)**을 잠그도록 재작성한다. "헤더 없으면 마스터 컨텍스트 반환", "로그인 라우트 없음(404)" 등.
   - 로그인/계정 발급 테스트(`test_account_and_login.py`)가 제거된 라우트에 의존하면, 그 라우트 검증분만 제거/수정. 계정 저장소 자체 단위테스트는 보존.

핵심 규칙:
- 레이어 경계: 인증 결정은 api/deps(경계)에서. domain 모델 시그니처(`UserContext`) 변경 금지.
- 조용한 실패 금지: "토큰 있으면 검증하다 실패 시 무시" 식 금지. 아예 검증을 하지 않고 명시적으로 마스터 컨텍스트를 반환한다. 주석으로 "데모: 인증 비활성, 항상 전체접근" 이유를 남겨라.
- 하위 호환: 이것은 **의도된 breaking change**(인증 제거). ADR에 한 줄 기록하라(`docs/ADR.md`에 "ADR-NNN 데모 모드: 인증 비활성화, get_current_user가 고정 마스터 컨텍스트 반환" 추가).

## Acceptance Criteria

```bash
cd da_h
.venv/bin/ruff check src && .venv/bin/mypy src
.venv/bin/python -m pytest -q src/tests/unit/test_auth.py
.venv/bin/python -m pytest -q -m "not integration"
```

## 검증 절차

1. 위 AC 실행.
2. 체크리스트:
   - Authorization 헤더 없이 보호 라우트 호출 시 401이 아니라 200/정상 동작(테스트로 잠금).
   - `/auth/login`이 사라졌다(404).
   - 조용한 실패 없는가? (검증을 건너뛴 이유를 주석으로 남겼는가)
   - ADR에 인증 비활성화를 기록했는가?
   - 시크릿을 코드/커밋에 넣지 않았는가?
3. 결과를 `phases/6-codeserver-pipeline/step1.result.json`에 기록:
   - 성공 → `{"status": "completed", "summary": "deps.get_current_user/require_master/require_ingest_admin가 토큰검증 없이 고정 마스터 UserContext(최고 AccessLevel) 반환. /auth/login 라우트 제거. access필터·부서가중 내부배선 유지(항상 전체접근). ADR-NNN 추가. test_auth 재작성."}`
   - 실패/차단 → 해당 스키마.

## 금지사항

- `UserContext`/`AccessLevel` domain 모델의 필드를 바꾸지 마라. 이유: search/ingest 다수가 의존 → 광범위 breaking.
- access_level 하드필터·부서 가중 **내부 로직 자체를 삭제하지 마라**. 이유: 되돌리기(운영 모드 복원) 가능성 보존. 마스터 컨텍스트 주입만으로 무력화하면 충분.
- 프론트엔드(`frontend/`)는 건드리지 마라. 이유: step 2가 담당.
- `phases/6-codeserver-pipeline/index.json`을 편집하지 마라. 결과는 `step1.result.json`에만.
- 기존 테스트를 깨뜨린 채 두지 마라(동작 변경분은 테스트를 갱신해 잠근다).
