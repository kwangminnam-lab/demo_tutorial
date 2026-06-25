import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ApiError, type ApiClient } from '../api/client'
import type { DiffResult } from '../api/types'
import { DiffPage } from './DiffPage'

/** diffUpload만 구현한 모킹 클라이언트(나머지는 이 페이지에서 안 쓴다). */
function fakeClient(overrides: Partial<ApiClient>): ApiClient {
  return {
    search: vi.fn(),
    ragAnswer: vi.fn(),
    ragStream: vi.fn(),
    diff: vi.fn(),
    diffUpload: vi.fn(),
    health: vi.fn(),
    ...overrides,
  } as unknown as ApiClient
}

/** 두 File 을 폼에 주입하고 비교를 누른다. */
function compareFiles(fileA: File, fileB: File) {
  fireEvent.change(screen.getByLabelText('기존 문서'), { target: { files: [fileA] } })
  fireEvent.change(screen.getByLabelText('수정 문서'), { target: { files: [fileB] } })
  fireEvent.click(screen.getByRole('button', { name: '비교' }))
}

const DIFF: DiffResult = {
  added: 1,
  deleted: 1,
  changed: 1,
  ops: [
    { op: 'equal', left: '같은 줄', right: '같은 줄' },
    { op: 'add', right: '추가된 줄' },
    { op: 'delete', left: '삭제된 줄' },
    {
      op: 'change',
      left: '값 100',
      right: '값 200',
      left_words: [
        { text: '값 ', changed: false },
        { text: '100', changed: true },
      ],
      right_words: [
        { text: '값 ', changed: false },
        { text: '200', changed: true },
      ],
    },
  ],
}

describe('DiffPage', () => {
  it('업로드 비교 시 add/delete/change 라인과 카운트를 렌더한다', async () => {
    const diffUpload = vi.fn().mockResolvedValue(DIFF)
    render(<DiffPage client={fakeClient({ diffUpload })} />)

    const fileA = new File(['A'], 'a.txt', { type: 'text/plain' })
    const fileB = new File(['B'], 'b.txt', { type: 'text/plain' })
    compareFiles(fileA, fileB)

    await waitFor(() =>
      expect(diffUpload).toHaveBeenCalledWith(fileA, fileB, expect.any(Object)),
    )

    const summary = await screen.findByLabelText('변경 요약')
    expect(summary).toHaveTextContent('추가 1')
    expect(summary).toHaveTextContent('삭제 1')
    expect(summary).toHaveTextContent('변경 1')

    const list = screen.getByLabelText('비교 결과')
    expect(list).toHaveTextContent('추가된 줄')
    expect(list).toHaveTextContent('삭제된 줄')
  })

  it('변경 라인 안의 바뀐 단어를 강조 표시한다', async () => {
    const diffUpload = vi.fn().mockResolvedValue(DIFF)
    const { container } = render(<DiffPage client={fakeClient({ diffUpload })} />)

    compareFiles(
      new File(['A'], 'a.txt', { type: 'text/plain' }),
      new File(['B'], 'b.txt', { type: 'text/plain' }),
    )

    await screen.findByLabelText('비교 결과')
    const changeBlock = container.querySelector('[data-op="change"]')
    expect(changeBlock).not.toBeNull()
    expect(changeBlock).toHaveTextContent('100')
    expect(changeBlock).toHaveTextContent('200')
    const emphasized = Array.from(
      changeBlock?.querySelectorAll('span') ?? [],
    ).filter((span) => span.style.fontWeight === '600')
    expect(emphasized.map((span) => span.textContent)).toEqual(['100', '200'])
  })

  it('diff API 에러 시 graceful 메시지를 보여준다(앱이 죽지 않음)', async () => {
    const diffUpload = vi
      .fn()
      .mockRejectedValue(new ApiError(500, '요청 실패: 500 Internal Server Error'))
    render(<DiffPage client={fakeClient({ diffUpload })} />)

    compareFiles(
      new File(['A'], 'a.txt', { type: 'text/plain' }),
      new File(['B'], 'b.txt', { type: 'text/plain' }),
    )

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('구성되지 않았습니다')
    expect(screen.getByRole('button', { name: '비교' })).toBeEnabled()
  })

  it('페이지 프리뷰가 있으면 페이지 모드가 기본으로 활성화되고 양쪽 이미지가 렌더된다', async () => {
    const diffUpload = vi.fn().mockResolvedValue({
      ...DIFF,
      page_previews_a: {
        '1': 'data:image/png;base64,AAA',
        '2': 'data:image/png;base64,BBB',
      },
      page_previews_b: {
        '1': 'data:image/png;base64,CCC',
      },
    } satisfies DiffResult)
    render(<DiffPage client={fakeClient({ diffUpload })} />)
    compareFiles(
      new File(['A'], 'a.pdf', { type: 'application/pdf' }),
      new File(['B'], 'b.pdf', { type: 'application/pdf' }),
    )

    await screen.findByLabelText('원본 페이지 프리뷰')
    // 좌측 컬럼 — 페이지 1, 2 / 우측 컬럼 — 페이지 1만 (각자 독립).
    const imgs = screen.getAllByRole('img', { name: /^페이지 [12]$/ })
    expect(imgs.length).toBe(3)  // A: 2장 + B: 1장
    // 페이지 카운트 배지 (A 2p, B 1p).
    expect(screen.getByText(/2p/)).toBeInTheDocument()
    expect(screen.getByText(/1p/)).toBeInTheDocument()

    // 텍스트 diff 탭으로 전환하면 비교 결과 테이블이 보인다.
    fireEvent.click(screen.getByRole('tab', { name: '텍스트 diff' }))
    expect(screen.getByLabelText('비교 결과')).toHaveTextContent('추가된 줄')
  })

  it('페이지 프리뷰가 없으면 텍스트 diff만 노출되고 토글이 숨겨진다', async () => {
    const diffUpload = vi.fn().mockResolvedValue(DIFF)
    render(<DiffPage client={fakeClient({ diffUpload })} />)
    compareFiles(
      new File(['A'], 'a.txt', { type: 'text/plain' }),
      new File(['B'], 'b.txt', { type: 'text/plain' }),
    )

    await screen.findByLabelText('비교 결과')
    expect(screen.queryByRole('tablist', { name: '비교 뷰 모드' })).toBeNull()
    expect(screen.queryByLabelText('원본 페이지 프리뷰')).toBeNull()
  })

  it('파일이 없으면 비교하지 않는다(버튼 비활성)', () => {
    const diffUpload = vi.fn()
    render(<DiffPage client={fakeClient({ diffUpload })} />)

    // 한쪽만 채워도 비활성 유지.
    fireEvent.change(screen.getByLabelText('기존 문서'), {
      target: { files: [new File(['A'], 'a.txt', { type: 'text/plain' })] },
    })
    expect(screen.getByRole('button', { name: '비교' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '비교' }))
    expect(diffUpload).not.toHaveBeenCalled()
  })
})
