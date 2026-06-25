import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ApiClient } from '../api/client'
import type { ParseResponse } from '../api/types'
import { parseStore } from '../lib/parseStore'
import { ParsePage } from './ParsePage'

const PDF = new File([new Uint8Array([1, 2, 3])], 'doc.pdf', { type: 'application/pdf' })

const PARSE_RESULT: ParseResponse = {
  filename: 'doc.pdf',
  doc_type: 'PDF',
  html: '<h1>제목</h1>',
  json_data: {},
  page_previews: {},
}

const NDJSON =
  JSON.stringify({
    filename: 'doc.pdf',
    doc_type: 'PDF',
    section_index: 0,
    level: 1,
    title: '대제목',
    path: ['대제목'],
    page: 1,
    text: 'a',
  }) +
  '\n' +
  JSON.stringify({
    filename: 'doc.pdf',
    doc_type: 'PDF',
    section_index: 1,
    level: 2,
    title: '소제목',
    path: ['대제목', '소제목'],
    page: 2,
    text: 'b',
  }) +
  '\n'

function fakeClient(over: Partial<ApiClient> = {}): ApiClient {
  return {
    parseUpload: vi.fn().mockResolvedValue(PARSE_RESULT),
    parseJsonl: vi.fn().mockResolvedValue(NDJSON),
    ...over,
  } as unknown as ApiClient
}

beforeEach(() => {
  parseStore.reset()
  URL.createObjectURL = vi.fn(() => 'blob:test')
  URL.revokeObjectURL = vi.fn()
})

afterEach(() => {
  parseStore.reset()
  vi.restoreAllMocks()
})

describe('ParsePage JSONL 다운로드', () => {
  it('JSONL 다운로드 버튼이 client.parseJsonl 결과로 Blob 저장을 트리거', async () => {
    const client = fakeClient()
    await parseStore.parse(client, PDF)
    render(<ParsePage client={client} />)

    const button = await screen.findByRole('button', { name: 'JSONL 다운로드' })
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    fireEvent.click(button)

    await waitFor(() => expect(URL.createObjectURL).toHaveBeenCalled())
    expect(clickSpy).toHaveBeenCalled()
    expect(client.parseJsonl).toHaveBeenCalled()
  })
})
