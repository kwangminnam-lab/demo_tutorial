import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeAll, describe, expect, it, vi } from 'vitest'
import type { ApiClient, Me } from '../api/client'
import { ExtractionPage } from './ExtractionPage'

// jsdom 은 objectURL 미구현 — DocPreview 가 파일 선택 시 호출하므로 스텁.
beforeAll(() => {
  vi.stubGlobal('URL', {
    ...URL,
    createObjectURL: vi.fn(() => 'blob:stub'),
    revokeObjectURL: vi.fn(),
  })
})

function fakeClient(): ApiClient {
  return {
    me: vi.fn().mockResolvedValue({ is_master: true, role: '마스터' } as Me),
    listExtractSchemas: vi.fn().mockResolvedValue([]),
  } as unknown as ApiClient
}

const PDF = new File([new Uint8Array([1, 2, 3])], 'scan.pdf', {
  type: 'application/pdf',
})

describe('ExtractionPage (drag-drop)', () => {
  it('업로드 영역에 drop하면 파일이 선택된다', async () => {
    render(<ExtractionPage client={fakeClient()} />)

    const input = screen.getByLabelText('이미지·PDF 업로드')
    fireEvent.drop(input.parentElement as HTMLElement, {
      dataTransfer: { files: [PDF] },
    })

    await waitFor(() =>
      expect(screen.getByText('scan.pdf')).toBeInTheDocument(),
    )
  })
})
