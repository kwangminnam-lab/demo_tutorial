/**
 * 사용자 환경설정 — localStorage 영속 (브라우저 로컬만, 서버 미전송).
 *
 * - 기본 LLM 모델 선택(gemma=gpt-oss / qwen3=사내 서빙). 둘 다 사내라 API 키 없음.
 * - 비활성 커넥터(source) 목록 — 검색·RAG에서 제외할 출처.
 * - 프로필 보호 비밀번호 해시 — 평문은 절대 저장하지 않음.
 *
 * 저장 키: `docux:user-settings`. 단일 JSON object로 직렬화.
 */

import type { LlmProvider } from '../api/client'
import type { SourceType } from '../api/types'

const KEY = 'docux:user-settings'

export interface UserSettings {
  /** 비활성 커넥터 (검색·RAG 결과에서 제외) */
  disabledSources: SourceType[]
  /** 사용자 선택 기본 LLM 모델 (chat 진입 시 prefill) */
  selectedProvider: LlmProvider
  /** 프로필 보호 비밀번호 SHA-256 hex (평문 저장 금지). 없으면 보호 비활성. */
  profilePasswordHash: string | null
}

const DEFAULT: UserSettings = {
  disabledSources: [],
  selectedProvider: 'gemma',
  profilePasswordHash: null,
}

export function loadSettings(): UserSettings {
  if (typeof window === 'undefined') return DEFAULT
  try {
    const raw = window.localStorage.getItem(KEY)
    if (!raw) return DEFAULT
    const parsed = JSON.parse(raw) as Partial<UserSettings>
    return { ...DEFAULT, ...parsed }
  } catch {
    return DEFAULT
  }
}

export function saveSettings(settings: UserSettings): void {
  if (typeof window === 'undefined') return
  window.localStorage.setItem(KEY, JSON.stringify(settings))
}

export function updateSettings(patch: Partial<UserSettings>): UserSettings {
  const current = loadSettings()
  const next: UserSettings = { ...current, ...patch }
  saveSettings(next)
  return next
}

/** 비밀번호를 SHA-256 hex로 해시 (Web Crypto API). 평문 저장 금지. */
export async function hashPassword(password: string): Promise<string> {
  const buf = new TextEncoder().encode(password)
  const digest = await crypto.subtle.digest('SHA-256', buf)
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
}

export async function verifyPassword(
  password: string,
  storedHash: string | null,
): Promise<boolean> {
  if (!storedHash) return true  // 보호 비활성 — 무조건 통과.
  return (await hashPassword(password)) === storedHash
}
