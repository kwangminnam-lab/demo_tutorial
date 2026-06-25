# Step 6: drag-drop-ui

## 읽어야 할 파일

먼저 아래를 읽고 기존 drag-drop 패턴과 업로드 UI를 파악하라:

- `docs/CONVENTIONS.md`
- `frontend/src/pages/ParsePage.tsx` (특히 `DropField` 컴포넌트 ~463행: `onDrop`/`onDragOver`/`event.dataTransfer.files` 완성형)
- `frontend/src/pages/ExtractionPage.tsx` (62행 plain `<input type="file">` — drag-drop 없음)
- `frontend/src/lib/uploadDoc.tsx` (119행 `UploadModal`의 plain `<input>` — drag-drop 없음)

## 의존 / 노출

- **의존**: `ParsePage.tsx`의 `DropField`가 이미 갖춘 drag-drop 동작(preventDefault, dataTransfer.files, 클릭 폴백).
- **노출**: 공용 `Dropzone` 컴포넌트(`frontend/src/components/Dropzone.tsx` 등) — drag-drop + 클릭 업로드. `ParsePage`·`ExtractionPage`·`UploadModal` 세 곳에서 재사용.

## 작업

F2: "문서추가 기능에 드래그앤드랍". ParsePage엔 이미 `DropField`로 완성돼 있다. 이를 **공용 컴포넌트로 추출**해 나머지 두 곳(`ExtractionPage`, `UploadModal`)에 적용한다.

1. `frontend/src/components/Dropzone.tsx`(신규): `ParsePage`의 `DropField` 로직을 일반화한 공용 컴포넌트.
   - props(계약): `onFiles: (files: FileList | File[]) => void`, `accept?: string`, `multiple?: boolean`, `label?: ReactNode`, `disabled?: boolean`.
   - 동작: dragover 하이라이트, drop 시 `preventDefault` + `onFiles(dataTransfer.files)`, 영역 클릭 시 숨은 `<input type=file>` 트리거. 기존 Tailwind 스타일 컨벤션 따름.
   - 접근성: 기존 코드의 `aria-label`/role 패턴 유지(테스트가 label로 요소를 찾는다).
2. `ParsePage.tsx`: `DropField`를 `Dropzone`로 교체(동작·기존 테스트 보존). 기존 ParsePage 테스트가 깨지면 안 됨 — label/콜백 호환 유지.
3. `ExtractionPage.tsx`: 62행(및 39행) plain `<input>` 업로드 자리를 `Dropzone`로 교체. AI OCR 업로드도 drag-drop 가능해진다. 기존 `onFile` 핸들러 시그니처에 맞게 `onFiles`에서 첫 파일을 넘기는 식으로 연결.
4. `lib/uploadDoc.tsx`의 `UploadModal`(119행): plain `<input>`을 `Dropzone`로 교체.
5. 테스트: 신규 `Dropzone` 단위 테스트(drop 이벤트 → onFiles 호출, 클릭 → input). `ExtractionPage`/`UploadModal`에 drag-drop이 생겼음을 잠그는 테스트 추가/갱신.

핵심 규칙:
- 조용한 실패 금지: drop된 파일 타입이 accept에 안 맞으면 무시하지 말고 사용자에게 알리거나 기존 검증 흐름을 타게 한다.
- 하위 호환: `ParsePage` 기존 동작·테스트 보존(공용화는 리팩터, 기능 동일).
- 빌드 게이트: `tsc -b`로 검증(`--noEmit` 금지 — references-only라 vacuous).
- 중복 제거: 세 곳의 업로드 입력이 하나의 `Dropzone`로 수렴.

## Acceptance Criteria

```bash
cd da_h/frontend
npx tsc -b
npx vitest run
```

## 검증 절차

1. 위 AC 실행(`tsc -b` + `vitest run` 통과).
2. 체크리스트:
   - `Dropzone`가 drop 이벤트에서 `onFiles` 호출, 클릭에서 input 트리거(테스트로 잠금).
   - ParsePage 기존 테스트 통과(회귀 없음).
   - ExtractionPage·UploadModal에서 drag-drop 동작(테스트).
   - 세 곳이 모두 `Dropzone` 재사용(`grep -rn "type=\"file\"" frontend/src`가 Dropzone 내부 1곳으로 수렴하는지 확인).
3. 결과를 `phases/6-codeserver-pipeline/step6.result.json`에 기록:
   - 성공 → `{"status": "completed", "summary": "components/Dropzone.tsx 신설(onFiles,accept,multiple,label,disabled — drag-drop+클릭). ParsePage DropField→Dropzone 교체, ExtractionPage·UploadModal plain input→Dropzone. Dropzone 단위테스트+적용처 테스트. tsc -b+vitest 통과."}`
   - 실패/차단 → 해당 스키마.

## 금지사항

- 백엔드(`src/`)를 건드리지 마라. 이유: 범위 밖.
- ParsePage의 기존 drag-drop 동작을 퇴화시키지 마라. 이유: 이미 동작 중 — 공용화는 무회귀 리팩터.
- `tsc --noEmit`만으로 통과 판정하지 마라. 이유: vacuous pass. `tsc -b` 필수.
- `phases/6-codeserver-pipeline/index.json`을 편집하지 마라. 결과는 `step6.result.json`에만.
