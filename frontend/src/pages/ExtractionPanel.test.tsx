import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { ApiClient, Me } from '../api/client'
import type { ExtractionSchema, ExtractResponse } from '../api/types'
import { ExtractionPanel } from './ExtractionPanel'

const SCHEMA: ExtractionSchema = {
  id: 1,
  name: '계약서',
  doc_type: 'contract',
  fields: [{ key: 'amount', type: 'money', required: true }],
  auto_generated: false,
}

const RESULT: ExtractResponse = {
  result: {
    doc_id: 'deadbeef',
    schema_id: 1,
    fields: [
      {
        key: 'amount',
        value: '1,200,000,000',
        page: 1,
        bbox: [10, 20, 100, 32],
        evidence_line_ids: [0],
        source: 'print',
        confidence: 0.95,
        needs_review: false,
      },
    ],
  },
  evidence_previews: { '1': 'data:image/png;base64,AAAA' },
}

function fakeClient(over: Partial<ApiClient> = {}): ApiClient {
  return {
    me: vi.fn().mockResolvedValue({ is_master: true, role: '마스터' } as Me),
    listExtractSchemas: vi.fn().mockResolvedValue([SCHEMA]),
    createExtractSchema: vi.fn(),
    updateExtractSchema: vi.fn(),
    deleteExtractSchema: vi.fn(),
    autoExtractSchema: vi.fn(),
    runExtraction: vi.fn().mockResolvedValue(RESULT),
    listExtractResults: vi.fn(),
    exportCorrectedExtraction: vi
      .fn()
      .mockResolvedValue({ export_path: 'extract/deadbeef.jsonl', export_error: null }),
    ...over,
  } as unknown as ApiClient
}

const PDF = new File([new Uint8Array([1, 2, 3])], 'doc.pdf', { type: 'application/pdf' })

describe('ExtractionPanel', () => {
  it('PDF면 스키마 선택·추출 버튼 표시', async () => {
    render(<ExtractionPanel client={fakeClient()} file={PDF} docType="PDF" />)
    await waitFor(() => expect(screen.getByLabelText('추출 스키마 선택')).toBeInTheDocument())
    expect(screen.getByRole('button', { name: '추출' })).toBeInTheDocument()
  })

  it('비-PDF·비이미지면 미지원 안내', async () => {
    render(<ExtractionPanel client={fakeClient()} file={null} docType="DOCX" />)
    await waitFor(() =>
      expect(screen.getByText(/PDF·스캔 이미지.*만 지원/)).toBeInTheDocument(),
    )
  })

  it('이미지(PNG)면 추출 가능 + 손글씨 모드 기본 ON', async () => {
    const img = new File([new Uint8Array([1])], 'scan.png', { type: 'image/png' })
    const runExtraction = vi.fn().mockResolvedValue(RESULT)
    render(<ExtractionPanel client={fakeClient({ runExtraction })} file={img} docType="PNG" />)
    await waitFor(() => expect(screen.getByLabelText('추출 스키마 선택')).toBeInTheDocument())
    expect((screen.getByLabelText('손글씨/스캔 모드') as HTMLInputElement).checked).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: '추출' }))
    await waitFor(() =>
      expect(runExtraction).toHaveBeenCalledWith(img, 1, { vlm: true, signal: expect.anything() }),
    )
  })

  it('마스터는 템플릿 만들기, 비마스터는 없음', async () => {
    const { unmount } = render(<ExtractionPanel client={fakeClient()} file={PDF} docType="PDF" />)
    await waitFor(() =>
      expect(screen.getByRole('button', { name: '템플릿 만들기' })).toBeInTheDocument(),
    )
    unmount()
    render(
      <ExtractionPanel
        client={fakeClient({ me: vi.fn().mockResolvedValue({ is_master: false } as Me) })}
        file={PDF}
        docType="PDF"
      />,
    )
    await waitFor(() => expect(screen.getByLabelText('추출 스키마 선택')).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: '템플릿 만들기' })).not.toBeInTheDocument()
  })

  it('추출 실행 → 값·근거 표시, 손글씨 토글이 vlm 전달', async () => {
    const runExtraction = vi.fn().mockResolvedValue(RESULT)
    render(<ExtractionPanel client={fakeClient({ runExtraction })} file={PDF} docType="PDF" />)
    await waitFor(() => expect(screen.getByLabelText('추출 스키마 선택')).toBeInTheDocument())

    fireEvent.click(screen.getByLabelText('손글씨/스캔 모드'))
    fireEvent.click(screen.getByRole('button', { name: '추출' }))

    await waitFor(() =>
      expect(runExtraction).toHaveBeenCalledWith(PDF, 1, { vlm: true, signal: expect.anything() }),
    )
    // 값은 보정 가능한 input으로 렌더된다(getByDisplayValue).
    await waitFor(() => expect(screen.getByDisplayValue('1,200,000,000')).toBeInTheDocument())
    expect(screen.getByText('1/1 추출')).toBeInTheDocument()
  })

  it('스키마 없으면 템플릿 만들기 CTA(마스터)', async () => {
    render(
      <ExtractionPanel
        client={fakeClient({ listExtractSchemas: vi.fn().mockResolvedValue([]) })}
        file={PDF}
        docType="PDF"
      />,
    )
    await waitFor(() => expect(screen.getByText(/등록된 템플릿이 없습니다/)).toBeInTheDocument())
    expect(screen.getAllByRole('button', { name: '템플릿 만들기' }).length).toBeGreaterThan(0)
  })

  it('템플릿 만들기→자동생성→표 폼 채움(설명/Multi)→수정→저장', async () => {
    const proposed: ExtractionSchema = {
      name: '계약서',
      doc_type: 'contract',
      auto_generated: true,
      fields: [{ key: 'amount', type: 'float', required: true, description: '계약 금액' }],
    }
    const saved: ExtractionSchema = { ...proposed, id: 7, auto_generated: false }
    const autoExtractSchema = vi.fn().mockResolvedValue(proposed)
    const createExtractSchema = vi.fn().mockResolvedValue(saved)
    render(
      <ExtractionPanel
        client={fakeClient({
          listExtractSchemas: vi.fn().mockResolvedValue([]),
          autoExtractSchema,
          createExtractSchema,
        })}
        file={PDF}
        docType="PDF"
      />,
    )

    // 템플릿 만들기로 진입(인라인) → 안에서 자동생성 클릭. (빈 상태라 헤더+본문 2개 → 첫 버튼)
    await waitFor(() =>
      expect(screen.getAllByRole('button', { name: '템플릿 만들기' }).length).toBeGreaterThan(0),
    )
    fireEvent.click(screen.getAllByRole('button', { name: '템플릿 만들기' })[0])
    fireEvent.click(await screen.findByRole('button', { name: /템플릿 자동생성/ }))

    await waitFor(() => expect(autoExtractSchema).toHaveBeenCalled())
    // 제안 필드가 표 폼에 채워진다(Field name / Description).
    await waitFor(() =>
      expect((screen.getByLabelText('필드 1 이름') as HTMLInputElement).value).toBe('amount'),
    )
    expect((screen.getByLabelText('필드 1 설명') as HTMLInputElement).value).toBe('계약 금액')

    // 설명 수정 + Multi 체크 후 저장 → createExtractSchema가 반영해 호출.
    fireEvent.change(screen.getByLabelText('필드 1 설명'), { target: { value: '총 계약 금액' } })
    fireEvent.click(screen.getByLabelText('필드 1 Multi'))
    fireEvent.click(screen.getByRole('button', { name: '템플릿 저장' }))

    await waitFor(() => expect(createExtractSchema).toHaveBeenCalled())
    const arg = (createExtractSchema.mock.calls[0] as unknown[])[0] as ExtractionSchema
    expect(arg.fields[0].description).toBe('총 계약 금액')
    expect(arg.fields[0].multi).toBe(true)
    expect(arg.auto_generated).toBe(false)
  })

  it('마스터: 선택 템플릿 수정 → prefill 후 updateExtractSchema 호출', async () => {
    const updateExtractSchema = vi.fn().mockResolvedValue({ ...SCHEMA, name: '계약서v2' })
    render(<ExtractionPanel client={fakeClient({ updateExtractSchema })} file={PDF} docType="PDF" />)

    // 마스터 + 선택된 스키마 → "수정" 버튼 노출.
    fireEvent.click(await screen.findByRole('button', { name: '선택한 템플릿 수정' }))

    // 편집 폼이 기존 값으로 prefill(이름=계약서).
    const nameInput = (await screen.findByLabelText('템플릿 이름')) as HTMLInputElement
    expect(nameInput.value).toBe('계약서')
    fireEvent.change(nameInput, { target: { value: '계약서v2' } })

    // "수정 저장" → updateExtractSchema(id, body) 호출.
    fireEvent.click(screen.getByRole('button', { name: '수정 저장' }))
    await waitFor(() => expect(updateExtractSchema).toHaveBeenCalled())
    const [id, body] = updateExtractSchema.mock.calls[0] as [number, ExtractionSchema]
    expect(id).toBe(1)
    expect(body.name).toBe('계약서v2')
  })

  it('결과 화면: 초기화면으로→onReset, 결과 이전으로→추출 폼 복귀', async () => {
    const onReset = vi.fn()
    render(<ExtractionPanel client={fakeClient()} file={PDF} docType="PDF" onReset={onReset} />)

    // 추출 실행 → 결과 표시.
    fireEvent.click(await screen.findByRole('button', { name: '추출' }))
    const back = await screen.findByRole('button', { name: '← 결과 이전으로' })

    // 초기화면으로 → 부모 onReset 호출(파일 비우기).
    fireEvent.click(screen.getByRole('button', { name: '초기화면으로' }))
    expect(onReset).toHaveBeenCalled()

    // 결과 이전으로 → 결과 비우고 추출 폼 복귀(추출 버튼 다시 노출).
    fireEvent.click(back)
    await waitFor(() =>
      expect(screen.getByRole('button', { name: '추출' })).toBeInTheDocument(),
    )
  })

  it('결과 JSONL 저장 버튼이 Blob 다운로드를 트리거', async () => {
    URL.createObjectURL = vi.fn(() => 'blob:x')
    URL.revokeObjectURL = vi.fn()
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    render(<ExtractionPanel client={fakeClient()} file={PDF} docType="PDF" />)

    fireEvent.click(await screen.findByRole('button', { name: '추출' }))
    fireEvent.click(await screen.findByRole('button', { name: 'JSONL 저장' }))

    expect(URL.createObjectURL).toHaveBeenCalled()
    expect(clickSpy).toHaveBeenCalled()
    clickSpy.mockRestore()
  })

  it('값 보정 → 수정 저장이 보정값으로 exportCorrectedExtraction 호출 + 경로 표시', async () => {
    const exportCorrectedExtraction = vi
      .fn()
      .mockResolvedValue({ export_path: 'extract/deadbeef.jsonl', export_error: null })
    render(
      <ExtractionPanel
        client={fakeClient({ exportCorrectedExtraction })}
        file={PDF}
        docType="PDF"
      />,
    )

    fireEvent.click(await screen.findByRole('button', { name: '추출' }))
    const input = (await screen.findByLabelText('amount 값')) as HTMLInputElement
    fireEvent.change(input, { target: { value: '999' } })
    fireEvent.click(screen.getByRole('button', { name: '수정 저장' }))

    await waitFor(() => expect(exportCorrectedExtraction).toHaveBeenCalled())
    const sent = exportCorrectedExtraction.mock.calls[0][0]
    expect(sent.fields[0].value).toBe('999') // 보정값이 전송됨
    await waitFor(() =>
      expect(screen.getByText(/저장됨: extract\/deadbeef\.jsonl/)).toBeInTheDocument(),
    )
  })

  it('onReset 미지정이면 초기화면으로 버튼 숨김', async () => {
    render(<ExtractionPanel client={fakeClient()} file={PDF} docType="PDF" />)
    fireEvent.click(await screen.findByRole('button', { name: '추출' }))
    await screen.findByRole('button', { name: '← 결과 이전으로' })
    expect(screen.queryByRole('button', { name: '초기화면으로' })).not.toBeInTheDocument()
  })
})
