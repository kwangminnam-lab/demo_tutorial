/**
 * 대시보드 — 시스템 현황 진입 화면.
 *
 * 데이터베이스·분석 엔진 상태 카드 + 최근 본 문서 표.
 * 상태는 **실데이터**(`client.health()`)로 채운다(목업 수치 금지) —
 * backend별 ok/down·backend명만 표기한다(권한·시크릿 미노출은 서버가 보장). 최근 본
 * 문서는 검색 결과 클릭 시 브라우저 로컬에 쌓인 기록(`recentDocs`)을 읽는다.
 *
 * `client`는 주입받는다(DI): 테스트는 모킹 클라이언트를 넘긴다. 헬스 조회 실패는
 * graceful하게만 표시한다(/healthz는 항상 200이라 여기 오면 네트워크 단절 등).
 */

import { useEffect, useRef, useState } from 'react'
import type { ApiClient } from '../api/client'
import type { BackendHealth, Health } from '../api/types'
import { downloadDoc, PdfPreviewModal } from '../lib/fileActions'
import { getRecentDocs, type RecentDoc } from '../lib/recentDocs'

type HealthStatus = 'idle' | 'loading' | 'error' | 'done'

/** backend 키 → 사람이 읽는 한국어 라벨. */
const BACKEND_LABEL: Record<string, string> = {
  embedder: '임베더',
  llm: 'LLM 추론',
  search_index: '검색 인덱스',
  vectorstore: '벡터 저장소',
  graph: '그래프 DB',
}

/** 섹션별 backend 분류 — 데이터베이스 / 분석 엔진. */
const DB_KEYS = ['vectorstore', 'graph'] as const
const ENGINE_KEYS = ['embedder', 'llm'] as const

export function DashboardPage({ client }: { client: ApiClient }) {
  const [health, setHealth] = useState<Health | null>(null)
  const [healthStatus, setHealthStatus] = useState<HealthStatus>('idle')
  const [recent, setRecent] = useState<RecentDoc[]>([])
  // PDF 행 클릭 시 인라인 미리보기 모달용. 다른 유형은 다운로드만.
  const [previewDoc, setPreviewDoc] = useState<RecentDoc | null>(null)

  // 경합 가드 + 언마운트 후 setState 방지.
  const requestIdRef = useRef(0)

  useEffect(() => {
    setRecent(getRecentDocs())
  }, [])

  useEffect(() => {
    const requestId = ++requestIdRef.current
    setHealthStatus('loading')
    client
      .health()
      .then((report) => {
        if (requestId === requestIdRef.current) {
          setHealth(report)
          setHealthStatus('done')
        }
      })
      .catch(() => {
        if (requestId === requestIdRef.current) {
          setHealthStatus('error')
        }
      })
    return () => {
      requestIdRef.current += 1
    }
  }, [client])

  return (
    <section className="mx-auto w-full max-w-6xl px-8 py-10">
      <h2 className="text-3xl font-bold tracking-tight text-[#111827]">
        통합 지식 관리 시스템
      </h2>
      <p className="mt-2 text-[15px] text-[#6b7280]">
        사내 자료를 권한 내에서 검색·질의·비교합니다.
      </p>

      {/* 데이터베이스 · 분석 엔진 (실 헬스) */}
      <div className="mt-8 grid grid-cols-1 gap-6 lg:grid-cols-2">
        <StatusCard
          title="데이터베이스"
          keys={DB_KEYS}
          health={health}
          status={healthStatus}
        />
        <StatusCard
          title="분석 엔진"
          keys={ENGINE_KEYS}
          health={health}
          status={healthStatus}
        />
      </div>

      {/* 최근 본 문서 */}
      <div className="mt-6 overflow-hidden rounded-xl border border-[#e5e7eb] bg-white shadow-sm">
        <div className="border-b border-[#e5e7eb] bg-[#f8f9fa] px-6 py-4 text-sm font-bold text-[#111827]">
          최근 본 문서
        </div>
        {recent.length === 0 ? (
          <p className="px-6 py-10 text-center text-sm text-[#9ca3af]">
            최근 본 문서가 없습니다. 검색 결과를 열면 여기에 표시됩니다.
          </p>
        ) : (
          <table className="w-full border-collapse">
            <thead>
              <tr className="border-b border-[#e5e7eb] text-left text-xs font-semibold text-[#9ca3af]">
                <th className="px-6 py-3">이름</th>
                <th className="w-32 px-6 py-3">형태</th>
                <th className="w-40 px-6 py-3">소스</th>
                <th className="w-32 px-6 py-3">날짜</th>
              </tr>
            </thead>
            <tbody>
              {recent.map((doc) => (
                <tr
                  key={doc.id}
                  onClick={() => {
                    // PDF면 인라인 미리보기, 그 외는 원본 다운로드(검색 결과와 동일 동작).
                    if (doc.type === 'PDF') {
                      setPreviewDoc(doc)
                    } else {
                      void downloadDoc(client, doc.id)
                    }
                  }}
                  className="cursor-pointer border-b border-[#f3f4f6] text-sm transition-colors last:border-0 hover:bg-[#f9fafb]"
                >
                  <td className="px-6 py-4 font-semibold text-[#111827]">
                    {doc.title}
                  </td>
                  <td className="px-6 py-4 text-[#6b7280]">{doc.type || '—'}</td>
                  <td className="px-6 py-4 text-[#6b7280]">{doc.source}</td>
                  <td className="px-6 py-4 text-[#6b7280]">{doc.date}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {previewDoc ? (
        <PdfPreviewModal
          docId={previewDoc.id}
          title={previewDoc.title}
          source={previewDoc.source}
          date={previewDoc.date}
          client={client}
          onClose={() => setPreviewDoc(null)}
        />
      ) : null}
    </section>
  )
}

/** backend 상태 목록 카드 — 키 묶음을 health에서 읽어 ok/down 배지로 렌더. */
function StatusCard({
  title,
  keys,
  health,
  status,
}: {
  title: string
  keys: readonly string[]
  health: Health | null
  status: HealthStatus
}) {
  return (
    <div className="rounded-xl border border-[#e5e7eb] bg-white shadow-sm">
      <div className="border-b border-[#e5e7eb] bg-[#f8f9fa] px-6 py-4 text-xs font-bold uppercase tracking-wider text-[#6b7280]">
        {title}
      </div>
      <div className="divide-y divide-[#f3f4f6]">
        {keys.map((key) => {
          const entry: BackendHealth | undefined = health?.backends?.[key]
          return (
            <div key={key} className="flex items-center justify-between p-5">
              <div className="flex items-center gap-3">
                <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-[#eef2ff] text-[#1d4ed8]">
                  <DotIcon />
                </span>
                <div>
                  <p className="text-sm font-semibold text-[#111827]">
                    {BACKEND_LABEL[key] ?? key}
                  </p>
                  <p className="text-xs text-[#9ca3af]">
                    {entry ? entry.backend : '—'}
                  </p>
                </div>
              </div>
              <StatusBadge
                ok={status === 'done' && (entry?.ok ?? false)}
                pending={status === 'loading' || status === 'idle'}
                okText="정상"
                downText="중단"
              />
            </div>
          )
        })}
      </div>
    </div>
  )
}

/** 상태 배지 — 정상(초록)/중단(빨강)/확인 중(회색). */
function StatusBadge({
  ok,
  pending,
  okText,
  downText,
}: {
  ok: boolean
  pending: boolean
  okText: string
  downText: string
}) {
  if (pending) {
    return (
      <span className="rounded px-2 py-0.5 text-xs font-bold text-[#9ca3af]">
        확인 중…
      </span>
    )
  }
  return ok ? (
    <span className="rounded bg-[#dcfce7] px-2 py-0.5 text-xs font-bold text-[#166534]">
      {okText}
    </span>
  ) : (
    <span className="rounded bg-[#fee2e2] px-2 py-0.5 text-xs font-bold text-[#991b1b]">
      {downText}
    </span>
  )
}

/** 작은 원통(데이터/엔진) 아이콘. */

function DotIcon() {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      aria-hidden
    >
      <ellipse cx="12" cy="5" rx="9" ry="3" />
      <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3" />
      <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5" />
    </svg>
  )
}
