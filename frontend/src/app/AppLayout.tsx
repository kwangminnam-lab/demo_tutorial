/**
 * 앱 레이아웃 — 좌측 사이드바 네비 + 라우트 콘텐츠(Outlet).
 *
 * 디자인은 design/ 목업(DocuX, 통일된 dx-sidebar) 기준 — 회색 사이드바(#f8f9fa),
 * 파랑 활성 알약(#eef2ff/#1d4ed8), 흰 본문. 네비는 기능 페이지(대시보드·검색·챗봇·
 * 문서 비교) 링크만 둔다. 페이지 로직은 각 페이지에 있다
 * (레이아웃에 중복 구현 금지). 본문 영역은 각 페이지가 자체 여백·상단 바를 소유한다.
 *
 * 사용자 표시는 `/v1/me`가 돌려준 user_id만 보여준다(로그인 제거, ADR-026).
 * 부서·권한은 retrieval 단계에서 서버가 권위로 강제하며(권한 인지) 클라이언트에
 * 노출하지 않는다 — 그래서 여기서도 표기만 하고 프론트가 권한 로직을 재구현하지 않는다.
 */

import { useEffect, useState, type ComponentType, type SVGProps } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import type { ApiClient, Me } from '../api/client'
import { UploadModal } from '../lib/uploadDoc'
import { loadSettings, verifyPassword } from '../lib/userSettings'
import { SettingsPage } from '../pages/SettingsPage'
import {
  IconChat,
  IconCompare,
  IconDashboard,
  IconExtract,
  IconParse,
  IconSearch,
} from './icons'

type NavItem = {
  to: string
  label: string
  end?: boolean
  Icon: ComponentType<SVGProps<SVGSVGElement>>
}

/** 네비 항목 — 경로 + 한국어 라벨 + 아이콘. 설정은 네비가 아니라 하단 프로필 옆 톱니로 간다. */
const NAV_ITEMS: readonly NavItem[] = [
  { to: '/', label: '대시보드', end: true, Icon: IconDashboard },
  { to: '/search', label: '검색', Icon: IconSearch },
  { to: '/chat', label: '챗봇', Icon: IconChat },
  { to: '/diff', label: '문서 비교', Icon: IconCompare },
  { to: '/parse', label: '문서 파싱', Icon: IconParse },
  { to: '/extract', label: 'AI OCR', Icon: IconExtract },
]

/** 네비 링크 한 줄 — 활성 시 연파랑 알약 + 진한 파랑 글자(dx-sidebar). */
function navLinkClass({ isActive }: { isActive: boolean }): string {
  const base =
    'flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm no-underline transition-colors'
  return isActive
    ? `${base} bg-[#eef2ff] font-semibold text-[#1d4ed8]`
    : `${base} font-medium text-[#374151] hover:bg-[#f3f4f6]`
}

export function AppLayout({
  client,
}: {
  client: ApiClient
}) {
  const [showUpload, setShowUpload] = useState(false)
  const [me, setMe] = useState<Me | null>(null)
  const [showSettings, setShowSettings] = useState(false)
  // 비밀번호 인증 게이트 — 보호 설정 시 평문 입력받아 hash 비교 후에만 모달 열림.
  const [pwPrompt, setPwPrompt] = useState(false)
  const [pwInput, setPwInput] = useState('')
  const [pwError, setPwError] = useState('')

  // /v1/me 항상 시도 — 토큰 검증 후 실제 사용자. 401·실패 시 graceful (null).
  useEffect(() => {
    let aborted = false
    client.me().then((m) => { if (!aborted) setMe(m) }).catch(() => { if (!aborted) setMe(null) })
    return () => { aborted = true }
  }, [client])

  return (
    <div className="flex h-screen overflow-hidden bg-white">
      <aside className="flex w-64 shrink-0 flex-col justify-between border-r border-[#e5e7eb] bg-[#f8f9fa]">
        <div>
          <div className="px-6 py-6">
            <NavLink
              to="/"
              end
              aria-label="대시보드로 이동"
              className="inline-flex items-center"
            >
              <img
                src="https://lh3.googleusercontent.com/aida-public/AB6AXuAkX-d3GjP01GxtqhAGCa9Ar1HJ3_stDxxLeF4yK5E4GMkgB3z8TAN8qsIQm4MjuogcCkNIP5r45anMBaZUG1o9wKeNOgkxBrPWICnyvhURvpqM6z5fZqWMSrBWSej5Qggkw_Lh175PxPcV-Shk-g1FNPJWeXWZ8EtqymNE7rf_DcQeYXamKCfyFwOQezfgPQpt2JZ7bSo7RdD7YUppKzEKRrh8X7_ZgQ50q4lrdjPyRJ9dl_xubEq4mTTgqpPn9f7Ca_F58q4MKu5KOQ"
                alt="DocuX"
                className="h-10 w-auto"
              />
            </NavLink>
          </div>

          {/* 문서 추가 — 사이드바 상단 글로벌 버튼. 어느 페이지에서든 모달로 띄움. */}
          <div className="px-3">
            <button
              type="button"
              onClick={() => setShowUpload(true)}
              aria-label="문서 추가"
              className="flex w-full cursor-pointer items-center justify-center gap-2 rounded-lg bg-[#1d4ed8] px-3 py-2.5 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-[#1e40af]"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                <line x1="12" y1="5" x2="12" y2="19" />
                <line x1="5" y1="12" x2="19" y2="12" />
              </svg>
              문서 추가
            </button>
          </div>

          <nav aria-label="주요 메뉴" className="mt-4 space-y-1 px-3">
            {NAV_ITEMS.map(({ to, label, end, Icon }) => (
              <NavLink key={to} to={to} end={end} className={navLinkClass}>
                {({ isActive }) => (
                  <>
                    <Icon
                      className={`shrink-0 ${isActive ? 'text-[#2563eb]' : 'text-[#9ca3af]'}`}
                    />
                    <span>{label}</span>
                  </>
                )}
              </NavLink>
            ))}
          </nav>
        </div>

        <div className="border-t border-[#e5e7eb] p-4">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-[#e5e7eb] text-sm font-bold text-[#1d4ed8]">
              {(me?.user_id ?? '?').slice(0, 1).toUpperCase()}
            </div>
            <div className="min-w-0 flex-1">
              <p
                aria-label="현재 사용자"
                className="truncate text-sm font-semibold text-[#111827]"
              >
                {me?.user_id ?? '확인 중…'}
              </p>
              <p className="truncate text-xs text-[#6b7280]">
                {me ? `${me.department} · ${me.role}` : '확인 중…'}
              </p>
            </div>
            <button
              type="button"
              aria-label="설정"
              onClick={() => {
                const stored = loadSettings().profilePasswordHash
                if (stored) {
                  setPwPrompt(true)
                  setPwError('')
                  setPwInput('')
                } else {
                  setShowSettings(true)
                }
              }}
              className="flex h-9 w-9 cursor-pointer items-center justify-center rounded-md text-[#6b7280] hover:bg-[#f3f4f6] hover:text-[#111827]"
              title="설정"
            >
              <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="3" />
                <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
              </svg>
            </button>
          </div>
        </div>
      </aside>

      <main className="flex min-w-0 flex-1 flex-col overflow-y-auto bg-white">
        <Outlet />
      </main>

      {showUpload ? (
        <UploadModal client={client} onClose={() => setShowUpload(false)} />
      ) : null}

      {showSettings ? (
        <SettingsPage
          client={client}
          isMaster={!!me?.is_master}
          onClose={() => setShowSettings(false)}
        />
      ) : null}

      {pwPrompt ? (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="비밀번호 확인"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
          onClick={() => setPwPrompt(false)}
        >
          <div
            className="w-full max-w-sm rounded-xl bg-white p-6 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="mb-3 text-base font-bold text-[#111827]">설정 접근 확인</h3>
            <p className="mb-3 text-xs text-[#6b7280]">설정·API 키는 비밀번호 인증 후 열람할 수 있습니다.</p>
            <form
              onSubmit={async (e) => {
                e.preventDefault()
                const ok = await verifyPassword(pwInput, loadSettings().profilePasswordHash)
                if (ok) {
                  setPwPrompt(false)
                  setPwInput('')
                  setPwError('')
                  setShowSettings(true)
                } else {
                  setPwError('비밀번호가 일치하지 않습니다')
                }
              }}
            >
              <input
                type="password"
                aria-label="비밀번호"
                autoFocus
                value={pwInput}
                onChange={(e) => setPwInput(e.target.value)}
                className="block w-full rounded-md border border-[#d1d5db] px-3 py-2 text-sm focus:border-[#1d4ed8] focus:outline-none"
              />
              {pwError && <p className="mt-2 text-xs text-[#b91c1c]">{pwError}</p>}
              <div className="mt-3 flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setPwPrompt(false)}
                  className="rounded-md border border-[#d1d5db] px-3 py-1.5 text-xs font-semibold text-[#374151] hover:bg-[#f3f4f6]"
                >
                  취소
                </button>
                <button
                  type="submit"
                  className="rounded-md bg-[#1d4ed8] px-3 py-1.5 text-xs font-semibold text-white hover:bg-[#1e40af]"
                >
                  확인
                </button>
              </div>
            </form>
          </div>
        </div>
      ) : null}
    </div>
  )
}
