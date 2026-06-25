import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ApiError, type ApiClient } from '../api/client'
import type { SearchHit } from '../api/types'
import { SearchPage } from './SearchPage'

/** search만 구현한 모킹 클라이언트(나머지 메서드는 이 페이지에서 안 쓴다). */
function fakeClient(search: ApiClient['search']): ApiClient {
  return {
    search,
    ragAnswer: vi.fn(),
    ragStream: vi.fn(),
    diff: vi.fn(),
    diffUpload: vi.fn(),
    facets: vi.fn().mockResolvedValue({ total: 0, by_source: {} }),
    ingestUpload: vi.fn(),
    health: vi.fn(),
    fetchFile: vi.fn(),
  } as unknown as ApiClient
}

const SAMPLE_HITS: SearchHit[] = [
  {
    doc_id: 'd1',
    text: '요금 정책 본문 스니펫',
    score: 0.912,
    // 파일명은 hit.title에서 와야 한다. source_url은 실제 적재 환경처럼 null —
    // 예전엔 source_url basename에 의존해 None이면 doc_id 해시가 제목으로 샜다(버그).
    title: 'Runway 2.0 요금정책.pdf',
    doc_type: 'PDF',
    metadata: {
      source: 'onedrive',
      access: 1,
      author: '김기획',
      author_department: 'AI플랫폼사업팀',
      source_url: null,
      ingested_at: '2026-05-26T08:46:36Z',
    },
  },
]

describe('SearchPage', () => {
  it('검색어·소스·포맷·날짜 제출 시 client.search를 올바른 인자로 호출한다', async () => {
    const search = vi.fn().mockResolvedValue([])
    render(<SearchPage client={fakeClient(search)} />)

    fireEvent.change(screen.getByLabelText('검색어'), {
      target: { value: '요금정책' },
    })
    fireEvent.change(screen.getByLabelText('소스'), {
      target: { value: 'onedrive' },
    })
    fireEvent.change(screen.getByLabelText('시작일'), { target: { value: '2026-01-01' } })
    fireEvent.change(screen.getByLabelText('종료일'), { target: { value: '2026-05-31' } })
    fireEvent.change(screen.getByLabelText('포맷'), { target: { value: 'PDF' } })
    fireEvent.click(screen.getByRole('button', { name: '검색' }))

    await waitFor(() =>
      expect(search).toHaveBeenCalledWith('요금정책', {
        source: 'onedrive',
        topK: 10,
        docType: 'PDF',
        dateFrom: '2026-01-01',
        dateTo: '2026-05-31',
      }),
    )
  })

  it('필터 미선택 시 source/docType/days를 undefined로 보낸다', async () => {
    const search = vi.fn().mockResolvedValue([])
    render(<SearchPage client={fakeClient(search)} />)

    fireEvent.change(screen.getByLabelText('검색어'), { target: { value: 'q' } })
    fireEvent.click(screen.getByRole('button', { name: '검색' }))

    await waitFor(() =>
      expect(search).toHaveBeenCalledWith('q', {
        source: undefined,
        topK: 10,
        docType: undefined,
        dateFrom: undefined,
        dateTo: undefined,
      }),
    )
  })

  it('빈 검색어는 호출하지 않는다', () => {
    const search = vi.fn().mockResolvedValue([])
    render(<SearchPage client={fakeClient(search)} />)

    fireEvent.change(screen.getByLabelText('검색어'), { target: { value: '   ' } })
    fireEvent.click(screen.getByRole('button', { name: '검색' }))

    expect(search).not.toHaveBeenCalled()
  })

  it('반환된 hit을 간략 정보(제목·요약·소스·작성자·작성일·유형)로 렌더한다', async () => {
    const search = vi.fn().mockResolvedValue(SAMPLE_HITS)
    render(<SearchPage client={fakeClient(search)} />)

    fireEvent.change(screen.getByLabelText('검색어'), { target: { value: '요금' } })
    fireEvent.click(screen.getByRole('button', { name: '검색' }))

    // 파일 제목(확장자 제거)은 제목 헤딩으로 렌더된다.
    expect(
      await screen.findByRole('heading', { name: 'Runway 2.0 요금정책' }),
    ).toBeInTheDocument()

    const list = screen.getByLabelText('검색 결과')
    expect(list).toHaveTextContent('PDF') // 문서 유형(확장자)
    expect(list).toHaveTextContent('요금 정책 본문 스니펫') // 1~2줄 요약
    expect(list).toHaveTextContent('OneDrive') // 원본 소스
    expect(list).toHaveTextContent('김기획') // 작성자
    expect(list).toHaveTextContent('2026.05') // 작성일자(YYYY.MM.DD)
  })

  it('빈 결과면 안내 메시지를 보여준다', async () => {
    const search = vi.fn().mockResolvedValue([])
    render(<SearchPage client={fakeClient(search)} />)

    fireEvent.change(screen.getByLabelText('검색어'), { target: { value: '없음' } })
    fireEvent.click(screen.getByRole('button', { name: '검색' }))

    expect(await screen.findByText('검색 결과가 없습니다.')).toBeInTheDocument()
  })

  it('에러 시 alert로 에러 메시지를 보여준다(토큰 미노출)', async () => {
    const search = vi.fn().mockRejectedValue(new ApiError(401, '요청 실패: 401 Unauthorized'))
    render(<SearchPage client={fakeClient(search)} />)

    fireEvent.change(screen.getByLabelText('검색어'), { target: { value: 'q' } })
    fireEvent.click(screen.getByRole('button', { name: '검색' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('401')
  })
})
