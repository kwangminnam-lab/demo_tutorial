/**
 * 페이지별 상태 sessionStorage 영속 — 탭 내 라우트 전환 시 상태 유지.
 *
 * sessionStorage 사용 이유:
 *  - 다른 탭과 격리(같은 사이트라도 별개 탭은 별개 store)
 *  - 새로고침에도 살아남음(다른 탭 닫혀도 영향 없음)
 *  - 탭을 닫으면 자동 삭제 (개인정보 잔여물 최소화)
 *
 * 일반화된 load/save 헬퍼. 각 페이지는 자기 키로 분리.
 */

export function loadPersisted<T>(key: string, fallback: T): T {
  if (typeof window === 'undefined') return fallback
  try {
    const raw = window.sessionStorage.getItem(key)
    if (!raw) return fallback
    return JSON.parse(raw) as T
  } catch {
    return fallback
  }
}

export function savePersisted<T>(key: string, value: T): void {
  if (typeof window === 'undefined') return
  try {
    window.sessionStorage.setItem(key, JSON.stringify(value))
  } catch {
    /* quota 초과 무시 — UX 영향 없음. */
  }
}

export function clearPersisted(key: string): void {
  if (typeof window === 'undefined') return
  window.sessionStorage.removeItem(key)
}
