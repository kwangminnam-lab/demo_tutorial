---
name: harness
description: Harness 개발 워크플로우. 소프트웨어 개발 작업을 자기완결적 step으로 설계하고 phases/ 구조(index.json + stepN.md)로 파일을 생성한 뒤 scripts/execute.py로 순차 자동 실행한다. 사용자가 구현 계획 수립, step 설계, 하네스 task 생성/실행을 요청할 때 사용.
---

이 프로젝트는 Harness 프레임워크를 사용한다. 소프트웨어 개발 작업을 step 단위로 쪼개 자동 실행한다. 아래 워크플로우에 따라 진행하라.

---

## 워크플로우

### A. 탐색

`/docs/` 하위 문서(PRD, ARCHITECTURE, ADR, CONVENTIONS)와 `CLAUDE.md`를 읽고 프로젝트의 목적·아키텍처·계약·규약을 파악한다. 기존 코드가 있으면 관련 모듈을 읽어 패턴을 이해한다. 필요시 Explore 에이전트를 병렬로 사용한다.

특히 확인할 것:
- PRD의 **핵심 기능과 Acceptance 기준**
- ARCHITECTURE의 **레이어 분리**와 **의존성 방향**, **요청 흐름**
- ADR의 **인터페이스 계약**(API/CLI/스키마)과 **테스트 전략**
- CONVENTIONS의 **에러 처리·네이밍·커밋 규약**

### B. 논의

인터페이스 계약, 데이터 스키마, 에러 모델, 외부 의존성(DB·서드파티 API 도입 여부), breaking change 여부 등 구체화해야 할 사항이 있으면 사용자에게 제시하고 논의한다.

### C. Step 설계

사용자가 구현 계획 작성을 지시하면 여러 step으로 나뉜 초안을 작성해 피드백을 요청한다.

설계 원칙:

1. **Scope 최소화** — 하나의 step에서 하나의 레이어 또는 기능 단위만 다룬다. 도메인 모델 + API + 영속화를 한 step에 욱여넣지 마라.
2. **자기완결성** — 각 step 파일은 독립된 Claude 세션에서 실행된다. "이전 대화에서 논의한 바와 같이" 같은 외부 참조는 금지한다.
3. **의존 관계 명시** — 이 step이 어떤 모듈/인터페이스에 의존하고 무엇을 노출하는지 step 문서에 명시한다. 이전 step이 만든 함수/클래스 시그니처를 적는다.
4. **시그니처 수준 지시** — 함수/클래스/API의 인터페이스(계약)만 제시하고 내부 구현은 에이전트 재량에 맡긴다. 단, 다음 핵심 규칙은 반드시 명시한다:
   - **레이어 경계**: domain은 외부 의존 금지, side effect는 adapters에
   - **에러 처리**: 조용한 실패 금지, 도메인 에러 vs 시스템 에러 구분
   - **하위 호환**: 공개 인터페이스 변경 시 breaking 여부 명시
5. **AC는 실행 가능한 커맨드** — "~가 동작해야 한다" 같은 추상적 서술이 아닌 실제 실행 가능한 검증 커맨드를 포함한다. 다음 중 하나 이상:
   - 단위 테스트: `pytest -q tests/unit/test_<module>.py`
   - 코드 품질: `ruff check . && mypy src` (타 언어는 해당 도구)
   - 통합 테스트: `pytest -q tests/integration/test_<feature>.py`
   - 빌드: `make build` 또는 `python -c "import <pkg>"`
   - CLI 스모크: `python -m <pkg> <cmd> --help`
   - 마이그레이션: `make migrate` 적용 후 스키마 검증
6. **주의사항은 구체적으로** — "조심해라" 대신 "X를 하지 마라. 이유: Y" 형식.
7. **네이밍** — step name은 kebab-case slug. 개발 도메인 예시:
   `scaffold`, `domain-model`, `config-loader`, `db-adapter`, `service-layer`, `api-endpoint`, `input-validation`, `error-handling`, `cli-command`, `integration`, `migration`, `refactor`, `docs`.

### D. 파일 생성

사용자가 승인하면 아래 파일들을 생성한다.

#### D-1. `phases/index.json` (전체 현황)

여러 task를 관리하는 top-level 인덱스. 이미 존재하면 `phases` 배열에 새 항목을 추가한다.

```json
{
  "phases": [
    {
      "dir": "0-mvp",
      "status": "pending"
    }
  ]
}
```

- `dir`: task 디렉토리명.
- `status`: `"pending"` | `"completed"` | `"error"` | `"blocked"`. execute.py가 실행 중 자동으로 업데이트한다.
- 타임스탬프(`completed_at`, `failed_at`, `blocked_at`)는 execute.py가 상태 변경 시 자동 기록한다. 생성 시 넣지 않는다.

#### D-2. `phases/{task-name}/index.json` (task 상세)

```json
{
  "project": "<프로젝트명>",
  "phase": "<task-name>",
  "steps": [
    { "step": 0, "name": "scaffold",         "status": "pending" },
    { "step": 1, "name": "domain-model",     "status": "pending" },
    { "step": 2, "name": "service-layer",    "status": "pending" },
    { "step": 3, "name": "api-endpoint",     "status": "pending" },
    { "step": 4, "name": "integration",      "status": "pending" }
  ]
}
```

필드 규칙:

- `project`: 프로젝트명 (CLAUDE.md 참조).
- `phase`: task 이름. 디렉토리명과 일치시킨다.
- `steps[].step`: 0부터 시작하는 순번.
- `steps[].name`: kebab-case slug.
- `steps[].status`: 초기값은 모두 `"pending"`.

**중요: index.json은 execute.py(하네스)가 전유한다. step을 실행하는 Claude 세션은 index.json을 절대 편집하지 않는다.** 대신 각 step 세션은 결과를 `phases/{task-name}/step{N}.result.json` 에 **유효한 JSON 객체 하나**로만 기록하고, execute.py가 이를 읽어 index.json을 안전하게 갱신한다. (과거 세션이 index.json을 손으로 편집하다 JSON을 깨뜨려 실행이 중단되던 문제를 제거하기 위함.)

`step{N}.result.json` 스키마:
- 성공 → `{"status": "completed", "summary": "<산출물 한 줄 요약>"}`
- 실패 → `{"status": "error", "error_message": "<구체적 에러>"}`
- 차단 → `{"status": "blocked", "blocked_reason": "<사유>"}`

상태 전이와 자동 기록 필드 (index.json에 기록하는 주체는 모두 execute.py):

| 전이 | 기록되는 필드 | 값의 출처 |
|------|-------------|----------|
| → `completed` | `status`, `summary`, `completed_at` | summary는 result 파일, 나머지 execute.py |
| → `error` | `status`, `error_message`, `failed_at` | error_message는 result 파일, 나머지 execute.py |
| → `blocked` | `status`, `blocked_reason`, `blocked_at` | blocked_reason은 result 파일, 나머지 execute.py |

`summary`는 step 완료 시 산출물을 한 줄로 요약한 것으로, execute.py가 다음 step 프롬프트에 컨텍스트로 누적 전달한다. **추가/수정된 모듈 경로**, **노출된 공개 함수/클래스 시그니처**, **새 의존성**을 포함하면 다음 step이 활용하기 좋다.

`created_at`은 execute.py가 최초 실행 시 task 레벨에 한 번만 기록한다. step 레벨의 `started_at`도 execute.py가 각 step 시작 시 자동 기록한다. result 파일은 `.gitignore` 처리되어 커밋되지 않는다.

#### D-3. `phases/{task-name}/step{N}.md` (각 step마다 1개)

```markdown
# Step {N}: {이름}

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 계약을 파악하라:

- `/docs/ARCHITECTURE.md`
- `/docs/ADR.md`
- `/docs/CONVENTIONS.md`
- {이전 step에서 생성/수정된 모듈 경로}

이전 step에서 만든 코드를 꼼꼼히 읽고, 인터페이스 계약과 레이어 경계를 이해한 뒤 작업하라.

## 의존 / 노출

- **의존**: {예: `src/<pkg>/domain/invoice.py`의 `Invoice` 모델, `services/billing.py`의 `charge()`}
- **노출**: {예: `api/routes.py`에 `POST /invoices` 핸들러, `services`에 `create_invoice()`}

## 작업

{구체적인 구현 지시. 파일 경로, 함수/클래스/API 시그니처, 핵심 로직 설명.
코드 스니펫은 인터페이스(계약) 수준만 제시하고 구현체는 에이전트에게 맡겨라.

반드시 박아넣을 규칙:
- 레이어 경계: domain은 외부 import 금지, side effect는 adapters에
- 에러 처리: 조용한 실패 금지, 도메인/시스템 에러 구분
- 하위 호환: 공개 인터페이스 변경 시 breaking 여부와 마이그레이션 명시}

## Acceptance Criteria

```bash
ruff check . && mypy src
pytest -q tests/unit/test_<module>.py
# 통합 테스트 (해당되면)
pytest -q tests/integration/test_<feature>.py
# 빌드/스모크 (해당되면)
python -m <pkg> <cmd> --help
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 체크리스트를 확인한다:
   - 레이어 경계를 지켰는가? (domain이 프레임워크/DB를 import하지 않는가?)
   - 에러를 삼키지 않았는가? (`except: pass` 없는가?)
   - 공개 인터페이스를 깨뜨리지 않았는가? (깼다면 명시·마이그레이션 했는가?)
   - 시크릿을 코드/커밋에 넣지 않았는가?
   - 동작 변경에 대응하는 테스트를 추가했는가?
3. 결과를 `phases/{task-name}/step{N}.result.json` 에 **유효한 JSON 객체 하나**로만 기록한다 (index.json은 절대 편집하지 마라 — 하네스가 관리):
   - 성공 → `{"status": "completed", "summary": "산출물 한 줄 요약 (모듈경로·공개 시그니처·새 의존성 포함)"}`
   - 수정 3회 시도 후에도 실패 → `{"status": "error", "error_message": "구체적 에러 내용"}`
   - 사용자 개입 필요 (자격증명, 외부 서비스 접근, 인프라 프로비저닝 등) → `{"status": "blocked", "blocked_reason": "구체적 사유"}` 후 즉시 중단

## 금지사항

- {이 step에서 하지 말아야 할 것. "X를 하지 마라. 이유: Y" 형식}
- `phases/{task-name}/index.json` 을 편집하지 마라. 이유: 하네스가 전유하며, 손편집은 JSON 손상으로 실행을 중단시킨다. 결과는 `step{N}.result.json` 에만 기록한다.
- 기존 테스트를 깨뜨리지 마라
- 이 step 범위 밖의 파일을 건드리지 마라
- 공개 인터페이스를 말없이 바꾸지 마라
```

### E. 실행

```bash
python3 scripts/execute.py {task-name}        # 순차 실행
python3 scripts/execute.py {task-name} --push  # 실행 후 push
```

execute.py가 자동으로 처리하는 것:

- `feat-{task-name}` 브랜치 생성/checkout
- 가드레일 주입 — CLAUDE.md + docs/*.md 내용을 매 step 프롬프트에 포함
- 컨텍스트 누적 — 완료된 step의 summary를 다음 step 프롬프트에 전달
- 자가 교정 — 실패 시 최대 3회 재시도하며, 이전 에러 메시지를 프롬프트에 피드백
- 2단계 커밋 — 코드 변경(`feat`)과 메타데이터(`chore`)를 분리 커밋
- 타임스탬프 — started_at, completed_at, failed_at, blocked_at 자동 기록
- 산출물 보호 — `node_modules/`, `dist/`, `build/`, `target/`, 가상환경, 캐시, 시크릿 파일 등은 자동 커밋 대상에서 제외

에러 복구:

- **error 발생 시**: `phases/{task-name}/index.json`에서 해당 step의 `status`를 `"pending"`으로 바꾸고 `error_message`를 삭제한 뒤 재실행한다.
- **blocked 발생 시**: `blocked_reason`에 적힌 사유(자격증명·외부 서비스 등)를 해결한 뒤, `status`를 `"pending"`으로 바꾸고 `blocked_reason`을 삭제한 뒤 재실행한다.
