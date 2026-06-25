/**
 * 문서 파싱 글로벌 store — diffStore와 같은 패턴.
 *
 * 진행 중인 파싱 요청을 컴포넌트 lifecycle 밖에 보유해 탭 이동에도 끊기지 않게 한다.
 * 결과(HTML/JSON)는 sessionStorage 영속 — remount/새로고침 시 같은 미리보기 복원.
 */

import type { ApiClient } from '../api/client'
import type { ParseResponse } from '../api/types'
import { clearPersisted, loadPersisted, savePersisted } from './pagePersistence'

const PERSIST_KEY = 'docux:parse-store'

export type ParseStatus = 'idle' | 'loading' | 'done' | 'error'

export interface ParseStoreState {
  file: File | null
  status: ParseStatus
  result: ParseResponse | null
  errorMessage: string
}

interface PersistedParse {
  status: ParseStatus
  result: ParseResponse | null
  errorMessage: string
}

const INITIAL: ParseStoreState = {
  file: null,
  status: 'idle',
  result: null,
  errorMessage: '',
}

let state: ParseStoreState = hydrate()
let activeController: AbortController | null = null
const listeners = new Set<() => void>()

function hydrate(): ParseStoreState {
  const p = loadPersisted<PersistedParse>(PERSIST_KEY, {
    status: 'idle', result: null, errorMessage: '',
  })
  return {
    ...INITIAL,
    status: p.status === 'loading' ? 'idle' : p.status,
    result: p.result,
    errorMessage: p.errorMessage,
  }
}

function persist(): void {
  savePersisted<PersistedParse>(PERSIST_KEY, {
    status: state.status, result: state.result, errorMessage: state.errorMessage,
  })
}

function emit(): void {
  for (const l of listeners) l()
}

function setState(patch: Partial<ParseStoreState>): void {
  state = { ...state, ...patch }
  persist()
  emit()
}

export const parseStore = {
  getSnapshot(): ParseStoreState {
    return state
  },
  subscribe(listener: () => void): () => void {
    listeners.add(listener)
    return () => {
      listeners.delete(listener)
    }
  },

  setFile(file: File | null): void {
    setState({ file })
  },

  reset(): void {
    if (activeController) {
      activeController.abort()
      activeController = null
    }
    state = { ...INITIAL }
    clearPersisted(PERSIST_KEY)
    emit()
  },

  async parse(client: ApiClient, file: File): Promise<void> {
    if (state.status === 'loading') return

    const controller = new AbortController()
    activeController = controller
    setState({ file, status: 'loading', result: null, errorMessage: '' })

    try {
      const result = await client.parseUpload(file, { signal: controller.signal })
      if (activeController !== controller) return
      setState({ status: 'done', result })
    } catch (error) {
      if (controller.signal.aborted) return
      if (activeController !== controller) return
      setState({ status: 'error', errorMessage: toErrorMessage(error) })
    } finally {
      if (activeController === controller) activeController = null
    }
  },
}

function toErrorMessage(error: unknown): string {
  if (error instanceof Error) {
    const status = (error as { status?: number }).status
    if (status === 422) return '지원하지 않는 파일 형식입니다.'
    if (status === 413) return '파일이 너무 큽니다 (최대 80 MiB).'
    if (status === 401) return '인증이 필요합니다. 다시 로그인해 주세요.'
    return error.message
  }
  return '파싱 중 오류가 발생했습니다.'
}
