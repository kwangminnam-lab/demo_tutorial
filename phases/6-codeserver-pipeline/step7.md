# Step 7: jsonl-ui

## 읽어야 할 파일

먼저 아래를 읽고 파싱/추출 프론트 흐름과 새 백엔드 계약을 파악하라:

- `docs/CONVENTIONS.md`
- `frontend/src/pages/ParsePage.tsx` (HTML/JSON 탭, 다운로드 버튼)
- `frontend/src/pages/ExtractionPage.tsx`, `frontend/src/pages/ExtractionPanel.tsx` (추출 결과 표시)
- `frontend/src/api/client.ts` (parse/extract API 호출부 — step 4가 추가한 .jsonl 엔드포인트)
- `frontend/src/api/types.ts` (`ParseResponse`/추출 타입)
- 백엔드 step 4 결과(`src/kms/api/v1/parse.py`·`extract.py`의 jsonl 다운로드 라우트 스키마)

## 의존 / 노출

- **의존**: step 4가 추가한 파싱/추출 `.jsonl` 다운로드 엔드포인트, step 3의 섹션 구조.
- **노출**: 프론트에서 결과를 `.jsonl`로 다운로드하는 버튼 + 문서 구조(heading 계층) 뷰.

## 작업

F4·F6 프론트: 다운로드를 `.json`→`.jsonl`로, 그리고 파싱 결과의 **구조(heading 계층)**를 보여준다.

1. `frontend/src/api/client.ts`: step 4의 jsonl 엔드포인트를 부르는 함수 추가(예: `downloadParseJsonl(file)`, `downloadExtractJsonl(docId)`), 응답을 Blob으로 받아 파일 저장. 기존 `createClient` 패턴·에러 처리(ApiError) 따름. 토큰은 step 2에서 제거됨(헤더 불요).
2. `ParsePage.tsx`: 기존 JSON 다운로드 버튼을 **`.jsonl` 다운로드**로 바꾸거나 추가. 파일명 `<원본>.jsonl`. 기존 HTML/JSON 미리보기 탭은 유지(미리보기는 그대로).
3. 구조 뷰: 파싱 결과의 섹션(heading 계층)을 트리/목차 형태로 표시(level 들여쓰기, title, page). 데이터는 step 4 jsonl 또는 별도 구조 응답에서. 최소 구현: heading 목록을 level 들여쓰기로 렌더.
4. `ExtractionPage`/`ExtractionPanel`: 추출 결과 `.jsonl` 다운로드 버튼 추가(필드당 1줄).
5. 테스트: 다운로드 버튼이 올바른 client 함수를 호출하고 Blob 저장을 트리거하는지(vitest, fetch mock). 구조 뷰가 heading을 들여쓰기로 렌더하는지.

핵심 규칙:
- 조용한 실패 금지: 다운로드/네트워크 실패 시 사용자에게 에러 표시(기존 ApiError 처리 흐름 재사용).
- 하위 호환: 기존 미리보기(HTML/JSON 탭)·추출 표시를 깨지 마라. jsonl은 추가 기능.
- 빌드 게이트: `tsc -b` 필수.

## Acceptance Criteria

```bash
cd da_h/frontend
npx tsc -b
npx vitest run
```

## 검증 절차

1. 위 AC 실행(`tsc -b` + `vitest run` 통과).
2. 체크리스트:
   - `.jsonl` 다운로드 버튼이 client 함수 호출 → Blob 저장(테스트로 잠금).
   - 구조 뷰가 heading level 들여쓰기로 렌더.
   - 기존 파싱/추출 화면 회귀 없음.
   - 다운로드 실패가 조용히 무시되지 않고 에러 표시.
3. 결과를 `phases/6-codeserver-pipeline/step7.result.json`에 기록:
   - 성공 → `{"status": "completed", "summary": "client.ts downloadParseJsonl/downloadExtractJsonl(Blob 저장). ParsePage .jsonl 다운로드 버튼+heading 구조 트리뷰. ExtractionPage/Panel .jsonl 다운로드. vitest 다운로드·구조뷰 테스트. tsc -b+vitest 통과."}`
   - 실패/차단 → 해당 스키마.

## 금지사항

- 백엔드(`src/`)를 건드리지 마라. 이유: step 4가 엔드포인트 제공 — 프론트만.
- 기존 HTML/JSON 미리보기를 제거하지 마라. 이유: 미리보기와 내보내기는 별개 기능.
- `tsc --noEmit`만으로 통과 판정하지 마라. `tsc -b` 필수.
- `phases/6-codeserver-pipeline/index.json`을 편집하지 마라. 결과는 `step7.result.json`에만.
