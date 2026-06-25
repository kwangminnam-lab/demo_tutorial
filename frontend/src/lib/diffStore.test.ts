/** diffStore 단위 테스트 — in-flight 보존 + 구독 + 영속. */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { diffStore } from './diffStore'

beforeEach(() => {
  diffStore.reset()
})

describe('diffStore', () => {
  it('초기 상태는 idle + 빈 결과', () => {
    const s = diffStore.getSnapshot()
    expect(s.status).toBe('idle')
    expect(s.result).toBeNull()
    expect(s.fileA).toBeNull()
    expect(s.fileB).toBeNull()
  })

  it('setFileA/setFileB가 store에 반영', () => {
    const f = new File(['x'], 'x.txt')
    diffStore.setFileA(f)
    expect(diffStore.getSnapshot().fileA).toBe(f)
  })

  it('compare 호출 시 store가 loading→done 전이', async () => {
    const diffUpload = vi.fn().mockResolvedValue({
      ops: [],
      added: 0,
      deleted: 0,
      changed: 0,
    })
    const client = { diffUpload } as unknown as Parameters<typeof diffStore.compare>[0]
    const a = new File(['a'], 'a.txt')
    const b = new File(['b'], 'b.txt')

    const promise = diffStore.compare(client, a, b)
    expect(diffStore.getSnapshot().status).toBe('loading')

    await promise
    expect(diffStore.getSnapshot().status).toBe('done')
    expect(diffStore.getSnapshot().result).toBeTruthy()
  })

  it('compare 도중 reset()이 호출되면 결과 무시 (취소)', async () => {
    let resolveUpload!: (value: unknown) => void
    const diffUpload = vi.fn(
      () => new Promise((res) => { resolveUpload = res }),
    )
    const client = { diffUpload } as unknown as Parameters<typeof diffStore.compare>[0]
    const a = new File(['a'], 'a.txt')
    const b = new File(['b'], 'b.txt')

    const promise = diffStore.compare(client, a, b)
    expect(diffStore.getSnapshot().status).toBe('loading')

    diffStore.reset()  // 취소 — controller.abort + state 초기화.
    resolveUpload({ ops: [], added: 0, deleted: 0, changed: 0 })
    await promise

    // reset 후 status는 idle 유지 (resolveUpload 결과는 무시됨).
    expect(diffStore.getSnapshot().status).toBe('idle')
    expect(diffStore.getSnapshot().result).toBeNull()
  })

  it('compare 실패 시 status=error + 에러 메시지 저장', async () => {
    const diffUpload = vi.fn().mockRejectedValue(
      Object.assign(new Error('Bad'), { status: 500 }),
    )
    const client = { diffUpload } as unknown as Parameters<typeof diffStore.compare>[0]
    const a = new File(['a'], 'a.txt')
    const b = new File(['b'], 'b.txt')

    await diffStore.compare(client, a, b)

    const s = diffStore.getSnapshot()
    expect(s.status).toBe('error')
    expect(s.errorMessage).toContain('구성되지 않았습니다')
  })

  it('구독자에게 상태 변경 알림', () => {
    const listener = vi.fn()
    const unsub = diffStore.subscribe(listener)

    diffStore.setFileA(new File(['x'], 'x.txt'))

    expect(listener).toHaveBeenCalled()
    unsub()
  })
})
