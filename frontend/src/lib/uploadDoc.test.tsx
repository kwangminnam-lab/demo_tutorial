import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { ApiClient } from '../api/client'
import { UploadModal } from './uploadDoc'

function fakeClient(): ApiClient {
  return {
    ingestUpload: vi.fn(),
  } as unknown as ApiClient
}

const PDF = new File([new Uint8Array([1, 2, 3])], 'report.pdf', {
  type: 'application/pdf',
})

describe('UploadModal (drag-drop)', () => {
  it('업로드 영역에 drop하면 파일이 선택되고 추가 버튼이 활성화된다', () => {
    render(<UploadModal client={fakeClient()} onClose={vi.fn()} />)

    const submit = screen.getByRole('button', { name: '추가' }) as HTMLButtonElement
    expect(submit.disabled).toBe(true)

    const input = screen.getByLabelText('업로드 파일')
    fireEvent.drop(input.parentElement as HTMLElement, {
      dataTransfer: { files: [PDF] },
    })

    expect(screen.getByText('report.pdf')).toBeInTheDocument()
    expect(submit.disabled).toBe(false)
  })
})
