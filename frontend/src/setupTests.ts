import '@testing-library/jest-dom'
import { beforeEach } from 'vitest'

function makeStorageStub(): Storage {
  const store = new Map<string, string>()
  return {
    get length() {
      return store.size
    },
    clear: () => store.clear(),
    getItem: (key) => (store.has(key) ? (store.get(key) as string) : null),
    key: (index) => Array.from(store.keys())[index] ?? null,
    removeItem: (key) => {
      store.delete(key)
    },
    setItem: (key, value) => {
      store.set(key, String(value))
    },
  }
}

// jsdom 26은 localStorage·sessionStorage를 기본 제공하지 않는다.
// 인메모리 폴리필 — 토큰·세션·페이지 상태 영속을 테스트한다.
if (typeof globalThis.localStorage === 'undefined') {
  Object.defineProperty(globalThis, 'localStorage', {
    value: makeStorageStub(),
    configurable: true,
  })
}
if (typeof globalThis.sessionStorage === 'undefined') {
  Object.defineProperty(globalThis, 'sessionStorage', {
    value: makeStorageStub(),
    configurable: true,
  })
}

// 테스트 간 격리 — 영속 store가 다른 테스트에 새지 않도록 매 테스트 전 초기화.
beforeEach(async () => {
  globalThis.localStorage?.clear()
  globalThis.sessionStorage?.clear()
  // 모듈 싱글톤 store들도 메모리 상태 초기화 (storage clear만으로는 안 됨).
  const { chatStore } = await import('./lib/chatStore')
  const { diffStore } = await import('./lib/diffStore')
  const { parseStore } = await import('./lib/parseStore')
  chatStore.newChat()
  diffStore.reset()
  parseStore.reset()
})
