/** parseStore 단위 테스트 — diff/chat store와 동일 패턴(lifecycle 무관 진행). */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { parseStore } from './parseStore'

beforeEach(() => {
  parseStore.reset()
})

describe('parseStore', () => {
  it('초기 상태 idle + 빈 결과', () => {
    const s = parseStore.getSnapshot()
    expect(s.status).toBe('idle')
    expect(s.result).toBeNull()
    expect(s.file).toBeNull()
  })

  it('parse 호출 시 loading→done 전이 + result 저장', async () => {
    const parseUpload = vi.fn().mockResolvedValue({
      filename: 'a.txt',
      doc_type: 'TXT',
      html: '<h1>a.txt</h1>',
      json_data: { type: 'MarkdownDoc', markdown: 'hello' },
    })
    const client = { parseUpload } as unknown as Parameters<typeof parseStore.parse>[0]
    const file = new File(['hello'], 'a.txt')

    const promise = parseStore.parse(client, file)
    expect(parseStore.getSnapshot().status).toBe('loading')
    await promise
    expect(parseStore.getSnapshot().status).toBe('done')
    expect(parseStore.getSnapshot().result?.filename).toBe('a.txt')
    expect(parseStore.getSnapshot().result?.html).toContain('<h1>')
  })

  it('parse 도중 reset 시 결과 무시 (취소)', async () => {
    let resolveUp!: (v: unknown) => void
    const parseUpload = vi.fn(() => new Promise((r) => { resolveUp = r }))
    const client = { parseUpload } as unknown as Parameters<typeof parseStore.parse>[0]
    const promise = parseStore.parse(client, new File(['x'], 'x.txt'))
    parseStore.reset()
    resolveUp({ filename: 'x.txt', doc_type: 'TXT', html: '', json_data: {} })
    await promise
    expect(parseStore.getSnapshot().status).toBe('idle')
    expect(parseStore.getSnapshot().result).toBeNull()
  })

  it('parse 실패 시 status=error + 메시지', async () => {
    const parseUpload = vi.fn().mockRejectedValue(
      Object.assign(new Error('Unsupported'), { status: 422 }),
    )
    const client = { parseUpload } as unknown as Parameters<typeof parseStore.parse>[0]
    await parseStore.parse(client, new File(['x'], 'x.png'))
    expect(parseStore.getSnapshot().status).toBe('error')
    expect(parseStore.getSnapshot().errorMessage).toContain('지원하지 않는')
  })

  it('구독자에게 변경 알림', () => {
    const listener = vi.fn()
    const unsub = parseStore.subscribe(listener)
    parseStore.setFile(new File(['x'], 'x.txt'))
    expect(listener).toHaveBeenCalled()
    unsub()
  })
})
