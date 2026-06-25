/**
 * JSONL(NDJSON) 클라이언트 유틸 — 순수 함수.
 *
 * - `parseJsonlSections`: 파싱 JSONL(섹션당 1줄, `/v1/parse/jsonl`)을 heading 구조
 *   목록으로 읽는다. 구조(목차) 뷰의 데이터 소스.
 * - `extractResultToJsonl`: 화면에 표시된 추출 결과를 그대로 필드당 1줄 NDJSON으로
 *   직렬화한다. 서버 `/v1/extract/run.jsonl` 재호출 대신 **표시된 결과를 그대로** 쓴다 —
 *   추출 재실행(Gemini 과금·비결정)을 피하고 다운로드본이 화면과 일치하도록. 키·형태는
 *   백엔드 `adapters/ingestion/jsonl.to_extract_jsonl`과 동일하게 맞춘다(계약).
 */

import type { ExtractionResult } from '../api/types'

/** 파싱 JSONL의 한 섹션(heading) — 구조 뷰 렌더용. */
export interface ParsedSection {
  sectionIndex: number
  level: number
  title: string
  page: number | null
  path: string[]
}

/**
 * 파싱 JSONL(NDJSON) 문자열을 섹션 목록으로 파싱한다. 빈 줄은 건너뛴다.
 * 손상된 줄은 `JSON.parse`가 던지며(조용한 누락 금지) 호출 측에서 에러로 표시한다.
 */
export function parseJsonlSections(ndjson: string): ParsedSection[] {
  const sections: ParsedSection[] = []
  for (const line of ndjson.split('\n')) {
    const trimmed = line.trim()
    if (!trimmed) continue
    const record = JSON.parse(trimmed) as {
      section_index?: number
      level?: number
      title?: string
      page?: number | null
      path?: string[]
    }
    sections.push({
      sectionIndex: record.section_index ?? sections.length,
      level: record.level ?? 0,
      title: record.title ?? '',
      page: record.page ?? null,
      path: Array.isArray(record.path) ? record.path : [],
    })
  }
  return sections
}

/**
 * 추출 결과를 필드당 1줄 NDJSON으로 직렬화한다(마지막 줄도 `\n` 종료).
 * 필드가 0건이면 메타만 담은 placeholder 1줄을 낸다(빈 결과 금지) — 백엔드와 동일.
 */
export function extractResultToJsonl(result: ExtractionResult): string {
  const fields = result.fields ?? []
  const records =
    fields.length > 0
      ? fields.map((field) => ({
          doc_id: result.doc_id,
          schema_id: result.schema_id ?? null,
          key: field.key,
          value: field.value,
          page: field.page ?? null,
          bbox: field.bbox ?? null,
          confidence: field.confidence ?? null,
          needs_review: field.needs_review,
        }))
      : [
          {
            doc_id: result.doc_id,
            schema_id: result.schema_id ?? null,
            key: null,
            value: null,
            page: null,
            bbox: null,
            confidence: null,
            needs_review: false,
          },
        ]
  return records.map((record) => JSON.stringify(record)).join('\n') + '\n'
}
