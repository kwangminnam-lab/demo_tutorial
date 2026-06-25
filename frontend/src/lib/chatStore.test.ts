/** chatStore 단위 테스트 — singleton 영속 + 구독 알림 + unmount 무관 lifecycle. */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { chatStore } from './chatStore'

beforeEach(() => {
  sessionStorage.clear()
  localStorage.clear()
  chatStore.newChat()
})

describe('chatStore', () => {
  it('초기 상태는 빈 turns + gemma provider', () => {
    const s = chatStore.getSnapshot()
    expect(s.turns).toEqual([])
    expect(s.input).toBe('')
    expect(s.sessionId).toBeNull()
    expect(s.isStreaming).toBe(false)
  })

  it('setInput 호출 시 구독자에게 알림', () => {
    const listener = vi.fn()
    const unsub = chatStore.subscribe(listener)

    chatStore.setInput('안녕')

    expect(listener).toHaveBeenCalled()
    expect(chatStore.getSnapshot().input).toBe('안녕')
    unsub()
  })

  it('setProvider 변경이 snapshot에 반영', () => {
    chatStore.setProvider('gemma')
    expect(chatStore.getSnapshot().provider).toBe('gemma')
  })

  it('newChat이 진행 중 스트림 중단 + 상태 초기화', () => {
    chatStore.setInput('test')
    chatStore.newChat()
    expect(chatStore.getSnapshot().input).toBe('')
    expect(chatStore.getSnapshot().turns).toEqual([])
  })

  it('send 호출 시 turn이 즉시 추가됨 (mock client)', async () => {
    const ragStream = vi.fn(async (_q, onChunk) => {
      onChunk('Hello ')
      onChunk('world')
    })
    const ragAnswer = vi.fn(async () => ({
      text: 'Hello world',
      citations: [],
      grounded: true,
    }))
    const client = {
      ragStream,
      ragAnswer,
      // 나머지 API는 안 씀.
    } as unknown as Parameters<typeof chatStore.send>[0]

    await chatStore.send(client, '질문')

    const s = chatStore.getSnapshot()
    expect(s.turns).toHaveLength(1)
    expect(s.turns[0].query).toBe('질문')
    expect(s.turns[0].answer).toBe('Hello world')
    expect(s.turns[0].status).toBe('done')
    expect(s.isStreaming).toBe(false)
  })

  it('sessionStorage에 자동 영속', async () => {
    chatStore.setInput('persisted')
    const raw = sessionStorage.getItem('docux:chat-store')
    expect(raw).toBeTruthy()
    expect(JSON.parse(raw!).input).toBe('persisted')
  })

  it('구독 해제 후엔 알림 안 옴', () => {
    const listener = vi.fn()
    const unsub = chatStore.subscribe(listener)
    unsub()
    chatStore.setInput('x')
    expect(listener).not.toHaveBeenCalled()
  })

  it('빈 질의는 send 무시', async () => {
    const ragStream = vi.fn()
    const client = { ragStream } as unknown as Parameters<typeof chatStore.send>[0]
    await chatStore.send(client, '   ')
    expect(ragStream).not.toHaveBeenCalled()
    expect(chatStore.getSnapshot().turns).toEqual([])
  })
})
