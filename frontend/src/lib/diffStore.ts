/**
 * 문서 비교(diff) 글로벌 store — 진행 중 비교가 탭 이동에도 끊기지 않게 lifecycle 밖에 둔다.
 *
 * 문제: DiffPage가 비교 요청을 보낸 사이 다른 라우트로 이동하면 컴포넌트가 unmount.
 * 진행 중 fetch는 계속되지만 setResult/setStatus가 사라진 컴포넌트를 향해 호출되어
 * 결과가 화면에 안 나타난다. 다시 돌아와도 빈 화면.
 *
 * 해결: 모듈 싱글톤. fetch + AbortController + 결과·에러 모두 store에 저장. DiffPage는
 * useSyncExternalStore로 구독만 — unmount해도 store는 살아 있고, 완료 시 store가
 * setState 대신 자신을 업데이트. remount 시 같은 상태로 즉시 hydrate.
 *
 * sessionStorage 영속: status/result/errorMessage만. File 객체는 직렬화 불가라
 * 메모리에만 보관(탭 새로고침 시는 결과만 살아남고 파일 입력은 비워짐).
 */

import type { ApiClient } from '../api/client'
import type { DiffResult } from '../api/types'
import { clearPersisted, loadPersisted, savePersisted } from './pagePersistence'

const PERSIST_KEY = 'docux:diff-store'

export type DiffStatus = 'idle' | 'loading' | 'done' | 'error'

export interface DiffStoreState {
  fileA: File | null
  fileB: File | null
  status: DiffStatus
  result: DiffResult | null
  errorMessage: string
}

interface PersistedDiff {
  status: DiffStatus
  result: DiffResult | null
  errorMessage: string
}

const INITIAL: DiffStoreState = {
  fileA: null,
  fileB: null,
  status: 'idle',
  result: null,
  errorMessage: '',
}

let state: DiffStoreState = hydrate()
let activeController: AbortController | null = null
const listeners = new Set<() => void>()

function hydrate(): DiffStoreState {
  const persisted = loadPersisted<PersistedDiff>(PERSIST_KEY, {
    status: 'idle',
    result: null,
    errorMessage: '',
  })
  // loading 상태는 hydrate 시 idle로 — 진행 중 요청은 이미 끊겼다고 가정 (탭 새로고침).
  return {
    ...INITIAL,
    status: persisted.status === 'loading' ? 'idle' : persisted.status,
    result: persisted.result,
    errorMessage: persisted.errorMessage,
  }
}

function persist(): void {
  savePersisted<PersistedDiff>(PERSIST_KEY, {
    status: state.status,
    result: state.result,
    errorMessage: state.errorMessage,
  })
}

function emit(): void {
  for (const l of listeners) l()
}

function setState(patch: Partial<DiffStoreState>): void {
  state = { ...state, ...patch }
  persist()
  emit()
}

export const diffStore = {
  getSnapshot(): DiffStoreState {
    return state
  },
  subscribe(listener: () => void): () => void {
    listeners.add(listener)
    return () => {
      listeners.delete(listener)
    }
  },

  setFileA(file: File | null): void {
    setState({ fileA: file })
  },
  setFileB(file: File | null): void {
    setState({ fileB: file })
  },

  isLoading(): boolean {
    return state.status === 'loading'
  },

  /** 진행 중 비교 중단 (취소 버튼 또는 새 비교 시작 시). */
  cancel(): void {
    if (activeController) {
      activeController.abort()
      activeController = null
    }
    if (state.status === 'loading') {
      setState({ status: 'idle' })
    }
  },

  /** 결과 초기화 + 입력 화면 복귀. */
  reset(): void {
    if (activeController) {
      activeController.abort()
      activeController = null
    }
    state = { ...INITIAL }
    clearPersisted(PERSIST_KEY)
    emit()
  },

  /**
   * 두 파일 비교 시작 — fetch가 store 안에서 진행되므로 컴포넌트 unmount와 무관하게 끝까지 진행.
   * 이미 진행 중이면 무시. 완료/에러 시 store 자동 업데이트 → 구독자(DiffPage)에 알림.
   */
  async compare(client: ApiClient, fileA: File, fileB: File): Promise<void> {
    if (state.status === 'loading') return

    const controller = new AbortController()
    activeController = controller
    setState({
      fileA,
      fileB,
      status: 'loading',
      result: null,
      errorMessage: '',
    })

    try {
      const result = await client.diffUpload(fileA, fileB, { signal: controller.signal })
      // 도중에 다른 compare가 시작되면(activeController 교체) 결과 무시.
      if (activeController !== controller) return
      setState({ status: 'done', result })
    } catch (error) {
      if (controller.signal.aborted) {
        // 사용자/내부 취소: 상태는 cancel()이 이미 설정.
        return
      }
      if (activeController !== controller) return
      setState({
        status: 'error',
        errorMessage: toErrorMessage(error),
      })
    } finally {
      if (activeController === controller) activeController = null
    }
  },
}

function toErrorMessage(error: unknown): string {
  // ApiError(상태 코드 포함)는 구체적 안내, 그 외 Error는 message, 미상 타입은 일반 안내.
  if (error instanceof Error) {
    const status = (error as { status?: number }).status
    if (status === 404) return '문서를 찾을 수 없습니다. doc_id를 확인해 주세요.'
    if (status === 403) return '두 문서 중 하나 이상에 접근 권한이 없습니다.'
    if (status !== undefined) {
      return '문서 비교 백엔드가 아직 구성되지 않았습니다. 잠시 후 다시 시도해 주세요.'
    }
    return error.message
  }
  return '문서 비교 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.'
}
