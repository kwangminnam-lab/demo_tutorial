# Step 2: remove-auth-frontend

## 읽어야 할 파일

먼저 아래를 읽고 프론트 인증 배선을 파악하라:

- `docs/CONVENTIONS.md`
- `frontend/src/auth/LoginGate.tsx` (로그인 게이트 UI)
- `frontend/src/auth/AuthContext.tsx` (인증 컨텍스트/토큰 보관)
- `frontend/src/App.tsx` (LoginGate/AuthProvider 래핑 위치)
- `frontend/src/main.tsx`
- `frontend/src/api/client.ts` (토큰 주입: `createClient(() => token)` 패턴, Authorization 헤더)
- `frontend/src/app/AppLayout.tsx` (로그아웃 버튼/사용자 표시 가능)
- `frontend/src/App.test.tsx`, 관련 vitest

## 의존 / 노출

- **의존**: 백엔드는 step 1에서 토큰 불요(항상 마스터)로 변경됨 — 프론트는 토큰을 보내지 않아도 된다.
- **노출**: 로그인 화면 없이 앱이 바로 메인으로 진입. `createClient`가 토큰 없이 동작(Authorization 헤더 생략, 기존에 이미 토큰 null이면 헤더 생략하는 분기 존재).

## 작업

사용자 결정: **로그인 전체 제거(데모)**. 프론트에서 로그인 게이트·인증 컨텍스트를 제거하고 앱이 바로 뜨게 한다.

1. `frontend/src/App.tsx`(및 `main.tsx`): `LoginGate`/`AuthProvider` 래핑을 제거하고 메인 레이아웃(`AppLayout`/라우트)을 직접 렌더한다.
2. `frontend/src/api/client.ts`: 클라이언트 생성 시 토큰 게터를 제거하거나 항상 `null`을 반환하도록 단순화한다. **`createClient`의 공개 시그니처를 깨면 호출부가 많으므로**, 토큰 게터 인자를 유지하되 항상 null을 넘기는 방식(호출부 최소 변경)을 우선 고려한다. Authorization 헤더 생략 분기는 이미 존재(테스트 `client.test.ts`의 "토큰 null이면 헤더 생략") — 이를 활용.
3. `frontend/src/auth/LoginGate.tsx`·`AuthContext.tsx`: 다른 곳에서 import가 모두 사라졌는지 확인 후 파일 삭제. 잔존 참조가 있으면 먼저 그 참조를 제거.
4. `AppLayout.tsx` 등에 로그아웃/현재 사용자 UI가 있으면 제거(로그인 개념 삭제와 일관).
5. 테스트: 로그인 플로우를 검증하던 테스트(`App.test.tsx` 등)는 "앱이 로그인 없이 바로 메인을 렌더"하도록 재작성. `client.test.ts`의 토큰 헤더 테스트 중 더 이상 유효하지 않은 것만 갱신(토큰 null=헤더 생략 테스트는 유지).

핵심 규칙:
- 조용한 실패 금지: 인증 제거로 죽은 코드(미사용 컨텍스트/훅)는 **삭제**하라. 주석 처리로 남기지 마라.
- 하위 호환: 프론트는 내부 앱이라 공개 API 계약 아님. 단 `createClient` 시그니처는 호출부 보호 위해 가급적 유지.
- 빌드 게이트: `tsc -b`(build mode)로 검증한다. `tsc --noEmit`은 root tsconfig가 references-only라 통과해도 무의미 — 반드시 `tsc -b`.

## Acceptance Criteria

```bash
cd da_h/frontend
npx tsc -b
npx vitest run
```

## 검증 절차

1. 위 AC 실행(`tsc -b` + `vitest run` 모두 통과).
2. 체크리스트:
   - 앱이 로그인 화면 없이 메인으로 진입(테스트로 잠금).
   - `LoginGate`/`AuthContext`로의 잔존 import 없음(`grep -rn "LoginGate\|AuthContext\|useAuth" frontend/src` 비어야 함).
   - 죽은 코드를 주석 아닌 삭제로 처리했는가?
   - 시크릿(토큰)을 코드에 박지 않았는가?
3. 결과를 `phases/6-codeserver-pipeline/step2.result.json`에 기록:
   - 성공 → `{"status": "completed", "summary": "App.tsx에서 LoginGate/AuthProvider 제거, 앱 바로 메인 진입. client.ts 토큰 게터 항상 null(Authorization 생략). auth/LoginGate.tsx·AuthContext.tsx 삭제. App.test 재작성. tsc -b + vitest 통과."}`
   - 실패/차단 → 해당 스키마.

## 금지사항

- 백엔드(`src/`)를 건드리지 마라. 이유: step 1이 담당, 인증은 이미 비활성.
- `createClient`의 호출부를 광범위하게 리팩터하지 마라. 이유: 범위 밖, breaking 위험. 토큰만 null화.
- `tsc --noEmit`만으로 통과 판정하지 마라. 이유: references-only tsconfig라 vacuous pass. 반드시 `tsc -b`.
- `phases/6-codeserver-pipeline/index.json`을 편집하지 마라. 결과는 `step2.result.json`에만.
