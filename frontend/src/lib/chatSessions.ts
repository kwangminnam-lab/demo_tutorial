/**
 * 챗봇 대화 세션 로컬 영속 — localStorage 기반.
 *
 * 브라우저 단위로만 저장(서버 미전송) — 다중 디바이스 동기화 없음.
 * 손상된 값은 빈 목록으로 폴백(보조 UX, 핵심 동작 영향 X).
 */

import type { Citation } from '../api/types'

export interface ChatTurnSnapshot {
  id: number
  query: string
  answer: string
  citations: Citation[]
  grounded: boolean | null
  status: 'streaming' | 'done' | 'error'
  errorMessage: string
}

export interface ChatSession {
  id: string
  title: string
  turns: ChatTurnSnapshot[]
  createdAt: number
  updatedAt: number
}

const KEY = 'docux:chat-sessions'
const LIMIT = 50
const TITLE_MAX = 40

export function listSessions(): ChatSession[] {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return (parsed as ChatSession[]).sort((a, b) => b.updatedAt - a.updatedAt)
  } catch {
    // 손상 → 빈 목록(다음 저장이 덮어쓴다).
    return []
  }
}

export function saveSession(session: ChatSession): void {
  try {
    const all = listSessions().filter((s) => s.id !== session.id)
    const next = [session, ...all].slice(0, LIMIT)
    localStorage.setItem(KEY, JSON.stringify(next))
  } catch {
    // localStorage 불가(프라이빗 모드 등) — 세션은 보조 기능이라 무시.
  }
}

export function deleteSession(id: string): void {
  try {
    const all = listSessions().filter((s) => s.id !== id)
    localStorage.setItem(KEY, JSON.stringify(all))
  } catch {
    // 무시.
  }
}

export function makeSessionId(): string {
  return `s${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

export function titleFrom(text: string): string {
  const t = text.trim().replace(/\s+/g, ' ')
  return t.length > TITLE_MAX ? `${t.slice(0, TITLE_MAX)}…` : t || '새 대화'
}
