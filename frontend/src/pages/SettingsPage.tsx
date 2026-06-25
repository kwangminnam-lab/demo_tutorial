/**
 * 설정 페이지 — 모달 형태. 톱니바퀴 클릭 시 열림.
 *
 * 기능:
 *  1) 비활성 커넥터(source) 토글 — 검색·RAG에서 해당 출처 제외.
 *  2) 프로필 보호 비밀번호 설정 — SHA-256 hash로만 저장(평문 X).
 *
 * 모든 데이터는 localStorage(`docux:user-settings`)만 사용. 서버 미전송.
 * (LLM 모델은 gemma=gpt-oss / qwen3=사내 서빙 둘 다 사내 엔드포인트라 사용자 API 키 없음 —
 *  과거 gemini용 API 키 입력 섹션은 제거됨.)
 */

import { useCallback, useEffect, useState, type FormEvent } from 'react'
import type { ApiClient, Member } from '../api/client'
import { ApiError } from '../api/client'
import type { SourceType } from '../api/types'
import {
  hashPassword,
  loadSettings,
  updateSettings,
  type UserSettings,
} from '../lib/userSettings'

const SOURCES: SourceType[] = ['onedrive', 'googledrive', 'slack']
const SOURCE_LABELS: Record<SourceType, string> = {
  onedrive: 'OneDrive',
  googledrive: 'Google Drive',
  slack: 'Slack',
}

export interface SettingsPageProps {
  onClose: () => void
  /** API 클라이언트 — 멤버 관리(마스터 전용) 호출용. */
  client: ApiClient
  /** 마스터(관리자) 여부 — API키·커넥터·멤버 관리 섹션 게이팅 (ADR-017). */
  isMaster: boolean
}

export function SettingsPage({ onClose, client, isMaster }: SettingsPageProps) {
  const [settings, setSettings] = useState<UserSettings>(() => loadSettings())
  const [pw1, setPw1] = useState('')
  const [pw2, setPw2] = useState('')
  const [savedMsg, setSavedMsg] = useState('')

  // ── 멤버 관리(마스터 전용) 상태 ──
  const [members, setMembers] = useState<Member[]>([])
  const [newEmail, setNewEmail] = useState('')
  const [newPw, setNewPw] = useState('')
  const [newDept, setNewDept] = useState('')
  const [memberMsg, setMemberMsg] = useState('')

  const loadMembers = useCallback(() => {
    if (!isMaster) return
    client
      .listMembers()
      .then(setMembers)
      .catch(() => setMembers([]))
  }, [client, isMaster])

  useEffect(() => {
    loadMembers()
  }, [loadMembers])

  async function handleAddMember(event: FormEvent) {
    event.preventDefault()
    setMemberMsg('')
    try {
      await client.addMember(newEmail.trim(), newPw, newDept.trim() || 'default')
      setNewEmail('')
      setNewPw('')
      setNewDept('')
      setMemberMsg('멤버 추가됨')
      loadMembers()
    } catch (error) {
      setMemberMsg(error instanceof ApiError ? error.message : '추가 실패')
    }
  }

  async function handleDeleteMember(id: number) {
    setMemberMsg('')
    try {
      await client.deleteMember(id)
      loadMembers()
    } catch (error) {
      setMemberMsg(error instanceof ApiError ? error.message : '삭제 실패')
    }
  }

  function refresh(next: UserSettings) {
    setSettings(next)
    setSavedMsg('저장됨')
    setTimeout(() => setSavedMsg(''), 1500)
  }

  function handleToggleSource(source: SourceType) {
    const current = new Set(settings.disabledSources)
    if (current.has(source)) {
      current.delete(source)
    } else {
      current.add(source)
    }
    const next = updateSettings({ disabledSources: Array.from(current) })
    refresh(next)
  }

  async function handleSavePassword(event: FormEvent) {
    event.preventDefault()
    if (pw1 !== pw2) {
      setSavedMsg('비밀번호가 일치하지 않습니다')
      return
    }
    if (pw1.length < 4) {
      setSavedMsg('4자 이상 입력하세요')
      return
    }
    const hash = await hashPassword(pw1)
    const next = updateSettings({ profilePasswordHash: hash })
    setPw1('')
    setPw2('')
    refresh(next)
  }

  function handleDisablePassword() {
    const next = updateSettings({ profilePasswordHash: null })
    refresh(next)
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="설정"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={onClose}
    >
      <div
        className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-xl bg-white p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-bold text-[#111827]">설정</h2>
          <div className="flex items-center gap-3">
            {savedMsg && <span className="text-xs text-[#15803d]">{savedMsg}</span>}
            <button
              type="button"
              onClick={onClose}
              aria-label="닫기"
              className="rounded-md px-2 py-1 text-sm text-[#6b7280] hover:bg-[#f3f4f6]"
            >
              ✕
            </button>
          </div>
        </div>

        {/* ── 커넥터 토글 (외부소스 활성/비활성, 마스터 전용) ── */}
        {isMaster && (
        <section className="mb-6">
          <h3 className="mb-2 text-sm font-semibold text-[#374151]">커넥터 활성/비활성</h3>
          <p className="mb-3 text-xs text-[#6b7280]">
            비활성화한 출처는 검색·RAG 결과에 나타나지 않습니다. 데이터는 적재된 상태로 유지됩니다.
          </p>
          <div className="space-y-2">
            {SOURCES.map((source) => {
              const disabled = settings.disabledSources.includes(source)
              return (
                <label
                  key={source}
                  className="flex cursor-pointer items-center justify-between rounded-lg border border-[#e5e7eb] px-3 py-2 hover:bg-[#f9fafb]"
                >
                  <span className="text-sm font-medium text-[#111827]">
                    {SOURCE_LABELS[source]}
                  </span>
                  <input
                    type="checkbox"
                    aria-label={`${source} 활성화`}
                    checked={!disabled}
                    onChange={() => handleToggleSource(source)}
                    className="h-4 w-4 cursor-pointer"
                  />
                </label>
              )
            })}
          </div>
        </section>

        )}

        {/* ── 멤버 관리 (마스터 전용) ── */}
        {isMaster && (
          <section className="mb-6">
            <h3 className="mb-2 text-sm font-semibold text-[#374151]">멤버 관리</h3>
            <p className="mb-3 text-xs text-[#6b7280]">
              마스터가 멤버 계정을 이메일·비밀번호로 추가하고 삭제합니다. 비밀번호는
              서버에 해시로만 저장됩니다(평문 미보관).
            </p>
            <form onSubmit={handleAddMember} className="mb-3 space-y-2">
              <input
                type="email"
                aria-label="멤버 이메일"
                value={newEmail}
                onChange={(e) => setNewEmail(e.target.value)}
                placeholder="이메일 아이디 (예: user@corp.com)"
                className="block w-full rounded-md border border-[#d1d5db] px-3 py-1.5 text-sm focus:border-[#1d4ed8] focus:outline-none"
              />
              <div className="flex gap-2">
                <input
                  type="password"
                  aria-label="멤버 비밀번호"
                  value={newPw}
                  onChange={(e) => setNewPw(e.target.value)}
                  placeholder="비밀번호 (8자 이상)"
                  className="min-w-0 flex-1 rounded-md border border-[#d1d5db] px-3 py-1.5 text-sm focus:border-[#1d4ed8] focus:outline-none"
                />
                <input
                  type="text"
                  aria-label="멤버 부서"
                  value={newDept}
                  onChange={(e) => setNewDept(e.target.value)}
                  placeholder="부서"
                  className="w-28 rounded-md border border-[#d1d5db] px-3 py-1.5 text-sm focus:border-[#1d4ed8] focus:outline-none"
                />
                <button
                  type="submit"
                  disabled={!newEmail.trim() || newPw.length < 8}
                  className="rounded-md bg-[#1d4ed8] px-3 py-1.5 text-xs font-semibold text-white hover:bg-[#1e40af] disabled:opacity-40"
                >
                  추가
                </button>
              </div>
              {memberMsg && <p className="text-xs text-[#6b7280]">{memberMsg}</p>}
            </form>
            <ul className="space-y-1">
              {members.map((member) => (
                <li
                  key={member.id}
                  className="flex items-center justify-between rounded-lg border border-[#e5e7eb] px-3 py-2"
                >
                  <span className="min-w-0 truncate text-sm text-[#111827]">
                    {member.email}
                    <span className="ml-2 text-xs text-[#6b7280]">
                      {member.department} · {member.is_master ? '마스터' : '멤버'}
                    </span>
                  </span>
                  {member.is_master ? (
                    <span className="text-xs text-[#9ca3af]">보호됨</span>
                  ) : (
                    <button
                      type="button"
                      onClick={() => handleDeleteMember(member.id)}
                      aria-label={`${member.email} 삭제`}
                      className="rounded-md border border-[#d1d5db] px-3 py-1 text-xs font-semibold text-[#b91c1c] hover:bg-[#fef2f2]"
                    >
                      삭제
                    </button>
                  )}
                </li>
              ))}
            </ul>
          </section>
        )}

        {/* ── 프로필 비밀번호 ── */}
        <section>
          <h3 className="mb-2 text-sm font-semibold text-[#374151]">프로필 보호 비밀번호</h3>
          <p className="mb-3 text-xs text-[#6b7280]">
            설정 후에는 프로필 페이지·키 편집에 비밀번호 인증이 필요합니다.
            {settings.profilePasswordHash ? ' (현재 보호 활성)' : ' (현재 비활성)'}
          </p>
          <form onSubmit={handleSavePassword} className="space-y-2">
            <input
              type="password"
              aria-label="새 비밀번호"
              value={pw1}
              onChange={(e) => setPw1(e.target.value)}
              placeholder="새 비밀번호 (4자 이상)"
              className="block w-full rounded-md border border-[#d1d5db] px-3 py-1.5 text-sm focus:border-[#1d4ed8] focus:outline-none"
            />
            <input
              type="password"
              aria-label="비밀번호 확인"
              value={pw2}
              onChange={(e) => setPw2(e.target.value)}
              placeholder="비밀번호 확인"
              className="block w-full rounded-md border border-[#d1d5db] px-3 py-1.5 text-sm focus:border-[#1d4ed8] focus:outline-none"
            />
            <div className="flex gap-2">
              <button
                type="submit"
                className="rounded-md bg-[#1d4ed8] px-3 py-1.5 text-xs font-semibold text-white hover:bg-[#1e40af]"
              >
                비밀번호 저장
              </button>
              {settings.profilePasswordHash && (
                <button
                  type="button"
                  onClick={handleDisablePassword}
                  className="rounded-md border border-[#d1d5db] px-3 py-1.5 text-xs font-semibold text-[#b91c1c] hover:bg-[#fef2f2]"
                >
                  보호 해제
                </button>
              )}
            </div>
          </form>
        </section>
      </div>
    </div>
  )
}
