import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ApiError, type ApiClient } from '../api/client'
import type { Answer } from '../api/types'
import { ChatPage } from './ChatPage'

/** ragStream·ragAnswer만 구현한 모킹 클라이언트(나머지는 이 페이지에서 안 쓴다). */
function fakeClient(overrides: Partial<ApiClient>): ApiClient {
  return {
    search: vi.fn(),
    ragAnswer: vi.fn(),
    ragStream: vi.fn(),
    diff: vi.fn(),
    health: vi.fn(),
    ...overrides,
  } as unknown as ApiClient
}

/** 입력에 질의를 넣고 전송한다. */
function ask(query: string) {
  fireEvent.change(screen.getByLabelText('질문'), { target: { value: query } })
  fireEvent.click(screen.getByRole('button', { name: '전송' }))
}

const GROUNDED_ANSWER: Answer = {
  text: '요약 본문',
  grounded: true,
  citations: [
    {
      source: 'onedrive',
      doc_id: 'd1',
      page: 3,
      snippet: '근거 스니펫',
      title: '근거 문서 제목',
    },
    {
      source: 'onedrive',
      doc_id: 'd2',
      page: 5,
      snippet: '두번째 근거',
      title: '두번째 문서',
    },
  ],
}

describe('ChatPage', () => {
  it('질의 전송 시 ragStream을 호출하고 조각을 누적 표시한다', async () => {
    const ragStream = vi
      .fn()
      .mockImplementation(
        (_query: string, onChunk: (chunk: string) => void) => {
          onChunk('안녕')
          onChunk('하세요')
          return Promise.resolve()
        },
      )
    const ragAnswer = vi.fn().mockResolvedValue(GROUNDED_ANSWER)
    render(<ChatPage client={fakeClient({ ragStream, ragAnswer })} />)

    ask('인사해줘')

    await waitFor(() => expect(ragStream).toHaveBeenCalledOnce())
    expect(ragStream.mock.calls[0][0]).toBe('인사해줘')
    expect(await screen.findByText('안녕하세요')).toBeInTheDocument()
  })

  it('출처 인용 목록을 표시하지 않는다(사용자 요청)', async () => {
    const ragStream = vi
      .fn()
      .mockImplementation((_q: string, onChunk: (c: string) => void) => {
        onChunk('요약 본문')
        return Promise.resolve()
      })
    const ragAnswer = vi.fn().mockResolvedValue(GROUNDED_ANSWER)
    render(<ChatPage client={fakeClient({ ragStream, ragAnswer })} />)

    ask('요약해줘')

    await waitFor(() => expect(ragStream).toHaveBeenCalledOnce())
    expect(await screen.findByText('요약 본문')).toBeInTheDocument()
    // 스트림 종료 후 grounded 보강이 끝나도 출처 목록은 렌더하지 않는다.
    await waitFor(() => expect(ragAnswer).toHaveBeenCalled())
    expect(screen.queryByLabelText('출처')).not.toBeInTheDocument()
  })

  it('답변의 [n] 마커를 본문 등장 순서대로 1부터 재매김 + 배지 클릭 시 fetchFile로 다운로드', async () => {
    const ragStream = vi
      .fn()
      .mockImplementation((_q: string, onChunk: (c: string) => void) => {
        // 원래 [2]가 먼저, [1]이 뒤에 등장 → 재매김으로 [1], [2]로 표시됨.
        onChunk('핵심 내용입니다 [2] 추가 설명 [1].')
        return Promise.resolve()
      })
    const ragAnswer = vi.fn().mockResolvedValue(GROUNDED_ANSWER)
    const fetchFile = vi.fn().mockResolvedValue({
      blob: new Blob(['x'], { type: 'text/plain' }),
      filename: '문서.docx',
    })
    render(
      <ChatPage client={fakeClient({ ragStream, ragAnswer, fetchFile })} />,
    )

    ask('질문')

    // 본문 텍스트는 그대로, [n]은 배지로 변환되어 사라짐.
    await screen.findByText(/핵심 내용입니다/)
    expect(screen.queryByText(/\[\d+\]/)).not.toBeInTheDocument()

    // citations 보강 후 배지 2개 — 본문 등장 순서대로 1·2로 재매김.
    await waitFor(() => expect(ragAnswer).toHaveBeenCalled())
    // 본문에 [2]가 먼저 등장 → display 1 = original 2 = citations[1] = '두번째 문서'
    const badge1 = await screen.findByRole('button', { name: /출처 1:.*두번째 문서/ })
    expect(badge1).toHaveTextContent('1')
    // [1]이 두 번째 → display 2 = original 1 = citations[0] = '근거 문서 제목'
    const badge2 = await screen.findByRole('button', { name: /출처 2:.*근거 문서 제목/ })
    expect(badge2).toHaveTextContent('2')

    // 클릭 시 fetchFile(file의 doc_id, download=true) 호출 — Authorization 헤더 포함.
    badge1.click()
    await waitFor(() => expect(fetchFile).toHaveBeenCalledWith('d2', true))
  })

  it('grounded=false면 근거 없음을 명시한다', async () => {
    const ragStream = vi.fn().mockResolvedValue(undefined)
    const ragAnswer = vi
      .fn()
      .mockResolvedValue({ text: '', grounded: false, citations: [] } as Answer)
    render(<ChatPage client={fakeClient({ ragStream, ragAnswer })} />)

    ask('답 없는 질문')

    expect(await screen.findByRole('note')).toHaveTextContent('근거 없음')
    expect(screen.queryByLabelText('출처')).not.toBeInTheDocument()
  })

  it('스트림 에러 시 alert로 에러 메시지를 보여준다(토큰 미노출)', async () => {
    const ragStream = vi
      .fn()
      .mockRejectedValue(new ApiError(401, '요청 실패: 401 Unauthorized'))
    const ragAnswer = vi.fn()
    render(<ChatPage client={fakeClient({ ragStream, ragAnswer })} />)

    ask('질문')

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('401')
    // 에러 시 출처 보강(ragAnswer)은 호출하지 않는다.
    expect(ragAnswer).not.toHaveBeenCalled()
  })

  it('빈 질의는 전송하지 않는다(버튼 비활성)', () => {
    const ragStream = vi.fn()
    render(<ChatPage client={fakeClient({ ragStream })} />)

    fireEvent.change(screen.getByLabelText('질문'), { target: { value: '   ' } })
    expect(screen.getByRole('button', { name: '전송' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '전송' }))
    expect(ragStream).not.toHaveBeenCalled()
  })
})
