import { describe, expect, it } from 'vitest'
import type { ExtractionResult } from '../api/types'
import { extractResultToJsonl, parseJsonlSections } from './jsonl'

describe('parseJsonlSections', () => {
  it('NDJSON 줄을 섹션으로 파싱하고 빈 줄은 건너뛴다', () => {
    const ndjson =
      JSON.stringify({ section_index: 0, level: 1, title: '제목', path: ['제목'], page: 1 }) +
      '\n' +
      JSON.stringify({ section_index: 1, level: 2, title: '소제목', path: ['제목', '소제목'], page: 2 }) +
      '\n'
    const sections = parseJsonlSections(ndjson)
    expect(sections).toHaveLength(2)
    expect(sections[0]).toMatchObject({ level: 1, title: '제목', page: 1 })
    expect(sections[1]).toMatchObject({ level: 2, title: '소제목', page: 2 })
  })

  it('빈 문자열이면 빈 목록', () => {
    expect(parseJsonlSections('')).toEqual([])
  })
})

describe('extractResultToJsonl', () => {
  it('필드당 1줄 NDJSON(키 고정)을 만들고 마지막 줄도 개행 종료', () => {
    const result: ExtractionResult = {
      doc_id: 'abc',
      schema_id: 3,
      fields: [
        {
          key: 'amount',
          value: '100',
          page: 1,
          bbox: [1, 2, 3, 4],
          evidence_line_ids: [0],
          source: 'print',
          confidence: 0.9,
          needs_review: false,
        },
      ],
    }
    const text = extractResultToJsonl(result)
    expect(text.endsWith('\n')).toBe(true)
    const lines = text.trimEnd().split('\n')
    expect(lines).toHaveLength(1)
    expect(JSON.parse(lines[0])).toEqual({
      doc_id: 'abc',
      schema_id: 3,
      key: 'amount',
      value: '100',
      page: 1,
      bbox: [1, 2, 3, 4],
      confidence: 0.9,
      needs_review: false,
    })
  })

  it('필드 0건이면 placeholder 1줄(빈 결과 금지)', () => {
    const text = extractResultToJsonl({ doc_id: 'x', schema_id: null, fields: [] })
    const lines = text.trimEnd().split('\n')
    expect(lines).toHaveLength(1)
    expect(JSON.parse(lines[0])).toMatchObject({ doc_id: 'x', key: null, needs_review: false })
  })
})
