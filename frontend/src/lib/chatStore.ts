/**
 * 챗봇 글로벌 store — 컴포넌트 lifecycle 밖에서 상태·진행 중 스트림을 보유한다.
 *
 * 문제: react-router는 라우트 전환 시 컴포넌트를 unmount. ChatPage가 useState로 갖던
 * turns/input/sessionId가 사라지고, 진행 중 ragStream의 onChunk 콜백이 사라진 setState를
 * 호출해 답변이 끊긴다.
 *
 * 해결: 모듈 레벨 싱글톤 store. AbortController + 누적 답변 + 모든 화면 상태가 store에
 * 산다. ChatPage는 useSyncExternalStore로 구독만 — unmount해도 store는 그대로,
 * remount 시 같은 상태로 즉시 hydrate. 스트림은 계속 흐른다.
 *
 * 추가: 모든 변경은 sessionStorage에 자동 영속(탭 새로고침에도 살아남음, 다른 탭과는 격리).
 */

import type { ApiClient, LlmProvider } from '../api/client'
import type { Citation } from '../api/types'
import {
  listSessions,
  makeSessionId,
  saveSession,
  titleFrom,
  type ChatSession,
} from './chatSessions'
import { loadSettings } from './userSettings'

const PERSIST_KEY = 'docux:chat-store'

export interface ChatTurn {
  id: number
  query: string
  answer: string
  citations: Citation[]
  grounded: boolean | null
  status: 'streaming' | 'done' | 'error'
  errorMessage: string
}

export interface ChatStoreState {
  input: string
  turns: ChatTurn[]
  sessionId: string | null
  sessionTitle: string
  provider: LlmProvider
  isStreaming: boolean
}

const INITIAL: ChatStoreState = {
  input: '',
  turns: [],
  sessionId: null,
  sessionTitle: '',
  provider: 'gemma',
  isStreaming: false,
}

let state: ChatStoreState = hydrate()
let turnIdCounter = state.turns.reduce((max, t) => Math.max(max, t.id), 0)
let activeController: AbortController | null = null
const listeners = new Set<() => void>()

function hydrate(): ChatStoreState {
  if (typeof window === 'undefined') return INITIAL
  try {
    const raw = window.sessionStorage.getItem(PERSIST_KEY)
    if (!raw) return { ...INITIAL, provider: loadSettings().selectedProvider }
    const parsed = JSON.parse(raw) as Partial<ChatStoreState>
    // 스트리밍 중이던 턴은 hydrate 시 done으로 강제 마감(스트림은 이미 끊겼을 수 있음).
    const turns = (parsed.turns ?? []).map((t) =>
      t.status === 'streaming' ? { ...t, status: 'done' as const } : t,
    )
    return {
      ...INITIAL,
      ...parsed,
      turns,
      isStreaming: false,
      provider: parsed.provider ?? loadSettings().selectedProvider,
    }
  } catch {
    return { ...INITIAL, provider: loadSettings().selectedProvider }
  }
}

function persist(): void {
  if (typeof window === 'undefined') return
  try {
    window.sessionStorage.setItem(PERSIST_KEY, JSON.stringify(state))
  } catch {
    /* quota/serialization 실패는 무시 — UX 영향 없음. */
  }
}

function emit(): void {
  for (const l of listeners) l()
}

function setState(patch: Partial<ChatStoreState>): void {
  state = { ...state, ...patch }
  persist()
  emit()
}

export const chatStore = {
  getSnapshot(): ChatStoreState {
    return state
  },
  subscribe(listener: () => void): () => void {
    listeners.add(listener)
    return () => {
      listeners.delete(listener)
    }
  },

  setInput(input: string): void {
    setState({ input })
  },

  setProvider(provider: LlmProvider): void {
    setState({ provider })
  },

  // 새 채팅 시작 — 진행 중 스트림 중단.
  newChat(): void {
    if (activeController) {
      activeController.abort()
      activeController = null
    }
    setState({ turns: [], input: '', sessionId: null, sessionTitle: '', isStreaming: false })
  },

  selectSession(session: ChatSession): void {
    if (activeController) {
      activeController.abort()
      activeController = null
    }
    setState({
      turns: session.turns,
      sessionId: session.id,
      sessionTitle: session.title,
      input: '',
      isStreaming: false,
    })
  },

  // 사용자가 중단(중단 버튼) — 진행 중인 controller만 abort.
  stop(): void {
    if (activeController) {
      activeController.abort()
      activeController = null
    }
  },

  /** 질의 전송 — store가 직접 ragStream 호출, 컴포넌트 lifecycle과 무관하게 진행. */
  async send(client: ApiClient, query: string): Promise<void> {
    const trimmed = query.trim()
    if (!trimmed || state.isStreaming) return

    // 세션 미존재면 즉시 생성.
    let sessionId = state.sessionId
    let sessionTitle = state.sessionTitle
    if (sessionId === null) {
      sessionId = makeSessionId()
      sessionTitle = titleFrom(trimmed)
    }

    const id = ++turnIdCounter
    const newTurn: ChatTurn = {
      id,
      query: trimmed,
      answer: '',
      citations: [],
      grounded: null,
      status: 'streaming',
      errorMessage: '',
    }
    setState({
      turns: [...state.turns, newTurn],
      sessionId,
      sessionTitle,
      input: '',
      isStreaming: true,
    })

    const controller = new AbortController()
    activeController = controller

    const settings = loadSettings()
    const disabledSources = settings.disabledSources

    try {
      await client.ragStream(
        trimmed,
        (chunk) => {
          // 함수형 패턴이 아닌 store 직접 업데이트(읽기-쓰기 race 없음 — 모든 변경은 메인 스레드).
          state = {
            ...state,
            turns: state.turns.map((t) =>
              t.id === id ? { ...t, answer: t.answer + chunk } : t,
            ),
          }
          persist()
          emit()
        },
        { signal: controller.signal, provider: state.provider, disabledSources },
      )
      // 스트림 종료 — done 표시 + 출처 보강(별도 호출).
      state = {
        ...state,
        turns: state.turns.map((t) => (t.id === id ? { ...t, status: 'done' as const } : t)),
        isStreaming: false,
      }
      persist()
      emit()

      void client
        .ragAnswer(trimmed, { provider: state.provider, disabledSources })
        .then((answer) => {
          state = {
            ...state,
            turns: state.turns.map((t) =>
              t.id === id ? { ...t, citations: answer.citations, grounded: answer.grounded } : t,
            ),
          }
          persist()
          emit()
        })
        .catch(() => {
          /* 출처 보강 실패는 본 답변에 영향 없음. */
        })

      // 세션 영속 (완료된 턴 포함).
      _persistSession()
    } catch (error) {
      if (controller.signal.aborted) {
        // 사용자 중단: 부분 답변 유지 + done.
        state = {
          ...state,
          turns: state.turns.map((t) =>
            t.id === id ? { ...t, status: 'done' as const } : t,
          ),
          isStreaming: false,
        }
        persist()
        emit()
        return
      }
      state = {
        ...state,
        turns: state.turns.map((t) =>
          t.id === id
            ? { ...t, status: 'error' as const, errorMessage: toErrorMessage(error) }
            : t,
        ),
        isStreaming: false,
      }
      persist()
      emit()
    } finally {
      if (activeController === controller) activeController = null
    }
  },
}

function _persistSession(): void {
  if (state.sessionId === null || state.turns.length === 0) return
  const existing = listSessions().find((s) => s.id === state.sessionId)
  saveSession({
    id: state.sessionId,
    title: state.sessionTitle || titleFrom(state.turns[0]?.query ?? ''),
    turns: state.turns,
    createdAt: existing?.createdAt ?? Date.now(),
    updatedAt: Date.now(),
  })
}

function toErrorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message
  }
  return '답변 생성 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.'
}
