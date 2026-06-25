/**
 * 백엔드 `/v1` 응답을 그대로 미러한 타입.
 *
 * 키·형태는 실제 라우트(`kms.api.v1.*`)·도메인 모델(`kms.domain.models`)과
 * 일치시킨다 — 백엔드 스키마가 바뀌면 여기도 함께 바꾼다(타입이 계약이다).
 */

/** 소스 종류 (`SourceType`, str Enum) — 출처 표기·검색 필터. */
export type SourceType = 'onedrive' | 'googledrive' | 'slack'

/**
 * 접근 레벨 (`AccessLevel`, IntEnum) — 1=임직원, 2=사장.
 * JSON에는 정수로 실린다(권한 인지 하드 필터; 클수록 넓은 권한).
 */
export type AccessLevel = 1 | 2

/** 적재 문서 메타데이터 (`DocumentMetadata`). `source`·`access`는 필수. */
export interface DocumentMetadata {
  source: SourceType
  access: AccessLevel
  author?: string | null
  author_department?: string | null
  source_url?: string | null
  ingested_at?: string | null
}

/** 검색 결과 1건 (`SearchHit`) — 제목·본문·점수·출처 메타. */
export interface SearchHit {
  doc_id: string
  text: string
  score: number
  metadata: DocumentMetadata
  /** 파일 제목(확장자 포함 원본 파일명). 검색 카드 제목으로 그대로 쓴다. */
  title: string
  tags?: string[]
  /** 문서 유형(확장자 대문자: PDF/XLSX/DOCX/PPTX...). 아이콘·유형 배지용. */
  doc_type?: string | null
}

/**
 * RAG 답변이 근거로 쓴 출처 1건 (`Citation`).
 * `page`·`slide_no`는 형식별로 있을 때만 채워진다(없으면 null/생략).
 */
export interface Citation {
  source: SourceType
  doc_id: string
  page?: number | null
  slide_no?: number | null
  snippet: string
  /** 표시명(툴팁) — snippet 첫 라인 또는 메타 제목. 없으면 doc_id로 폴백. */
  title?: string | null
  /** 원본 다운로드/이동 URL. 없으면 `/v1/files/{doc_id}?download=true`로 폴백. */
  source_url?: string | null
}

/**
 * RAG 답변 (`Answer`) — 본문 + 출처 인용 + 근거 기반 여부.
 * `grounded=false`면 검색 근거 0건(자유생성 없이 반환)이라 `citations`는 비어 있다.
 */
export interface Answer {
  text: string
  citations: Citation[]
  grounded: boolean
}

/** 변경 라인 내 단어 조각 (`WordSpan`) — 단어 단위 강조용. */
export interface WordSpan {
  text: string
  changed: boolean
}

/**
 * 라인 단위 diff 연산 (`DiffOp`).
 * `change`일 때만 `left_words`·`right_words`가 채워진다(그 외에는 null/생략).
 */
export interface DiffOp {
  op: 'equal' | 'add' | 'delete' | 'change'
  left?: string | null
  right?: string | null
  left_words?: WordSpan[] | null
  right_words?: WordSpan[] | null
}

/** 문서 비교 결과 (`DiffResult`) — 라인 연산 목록 + 추가/삭제/변경 라인 수. */
export interface DiffResult {
  ops: DiffOp[]
  added: number
  deleted: number
  changed: number
  /** sha8 → data URL (`data:image/...;base64,...`) — `[IMAGE sha=...]` 마커 렌더용. */
  image_blobs?: Record<string, string>
  /**
   * 원본 문서 A의 페이지 단위 PNG 프리뷰 (page_number(1-base) → data URL).
   * PDF 등 지원 포맷에서만 채워짐. 비교 본체와 별개의 보조 데이터.
   */
  page_previews_a?: Record<string, string>
  /** 원본 문서 B의 페이지 단위 PNG 프리뷰. */
  page_previews_b?: Record<string, string>
}

/** 문서 파싱 응답 (`POST /v1/parse/upload`) — HTML 미리보기 + JSON 원본 데이터 + 페이지 프리뷰. */
export interface ParseResponse {
  filename: string
  doc_type: string
  html: string
  json_data: Record<string, unknown>
  /** 페이지 번호(1-base, JSON에서 string 키) → PNG data URL. 미지원·실패 시 빈 객체. */
  page_previews?: Record<string, string>
  /** (문자 오프셋, 페이지 번호) 쌍 목록 — MarkdownDoc IR에서만 채워짐. */
  page_map?: Array<[number, number]>
}

// ── 필드추출(IDP, ADR-024) ──────────────────────────────────────────────

/** 추출 스키마 한 필드 정의. */
export interface SchemaField {
  key: string
  type: string  // text | number | date | money
  description?: string | null
  required: boolean
  multi?: boolean  // 한 필드가 여러 값(리스트)을 가질 수 있는가. 추출 연동은 후속.
}

/** 추출 스키마(필드 목록 템플릿). */
export interface ExtractionSchema {
  id?: number | null
  name: string
  doc_type?: string | null
  fields: SchemaField[]
  auto_generated: boolean
  created_by?: string | null
  created_at?: string | null
}

/** 추출 결과 한 필드 — 값 + 근거 라인 + bbox + 신뢰도. */
export interface ExtractedField {
  key: string
  value: string | null
  page?: number | null
  /** (x0,y0,x1,y1) PDF 좌표 — 근거 사각형. 미확정이면 null. */
  bbox?: [number, number, number, number] | null
  evidence_line_ids: number[]
  source: string  // print | handwriting
  confidence?: number | null
  /** 손글씨·저신뢰 — 사람 확인 필요(자동 확정 금지). */
  needs_review: boolean
}

/** 추출 결과(문서 1건 × 스키마 1개). */
export interface ExtractionResult {
  id?: number | null
  doc_id: string
  schema_id?: number | null
  fields: ExtractedField[]
  created_by?: string | null
  created_at?: string | null
}

/** 추출 실행 응답 — 결과 + 페이지별 근거(B-Box) PNG. */
export interface ExtractResponse {
  result: ExtractionResult
  /** 페이지 번호(1-base, string 키) → 근거 사각형이 칠해진 PNG data URL. */
  evidence_previews: Record<string, string>
}

/** 보정 결과 영속 응답 — export_root(공유 PVC)에 쓰인 상대경로 또는 실패 사유. */
export interface ExtractExportResult {
  export_path: string | null
  export_error: string | null
}

/** 구성요소별 헬스 (`/healthz`의 `backends` 값). */
export interface BackendHealth {
  backend: string
  ok: boolean
  detail?: string
}

/** 헬스체크 (`GET /healthz`). down이어도 HTTP 200 + `status: "degraded"`. */
export interface Health {
  status: 'ok' | 'degraded'
  backends: Record<string, BackendHealth>
}
