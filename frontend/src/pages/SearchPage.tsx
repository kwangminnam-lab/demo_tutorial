/**
 * 통합 검색 페이지.
 *
 * 검색어(+선택 소스 필터·top_k) 제출 → `client.search` 호출 → 결과 목록 표시.
 * 권한 인지 필터·부서 가중은 **서버가 권위**다 — 프론트는 결과를 그대로 보여주기만
 * 하고 권한 로직을 재구현하지 않는다(이중 로직·불일치 방지).
 *
 * 화면은 두 상태로 나뉜다:
 *  - 초기(idle): design/search_init.html — 중앙 정렬 히어로(큰 검색 바 + 추천 검색어
 *    칩 + 안내 카드 + 시스템 상태 푸터).
 *  - 검색 후(loading/done/error): design/search_result.html — 상단 검색 바 밴드 +
 *    필터 밴드 + 결과 카드 목록.
 * 두 상태는 같은 폼 상태(query·source·topK)와 `performSearch`를 공유한다.
 *
 * `client`는 주입받는다(DI): 테스트는 모킹한 클라이언트를, 실 배선(라우팅 step)은
 * `createClient(() => token)`을 넘긴다. 페이지는 데이터 소스에 비결합·순수하다.
 */

import { useEffect, useRef, useState, type FormEvent } from 'react'
import { loadPersisted, savePersisted } from '../lib/pagePersistence'
import type { ApiClient, SourceFacets } from '../api/client'
import { ApiError } from '../api/client'
import type { SearchHit, SourceType } from '../api/types'
import { SearchResults, type SearchStatus } from './SearchResults'

/** 소스 코드값 → 사람이 읽는 한국어 라벨(facets 표시용). */
const SOURCE_LABEL: Record<string, string> = {
  onedrive: 'OneDrive',
  googledrive: 'Google Drive',
  slack: 'Slack',
}

/** 소스 필터 드롭다운 옵션 — 빈 값은 "전체"(필터 없음). */
const SOURCE_OPTIONS: readonly SourceType[] = ['onedrive', 'googledrive', 'slack']

/** 포맷 필터 옵션 — 대문자 확장자. 빈 값은 전체. */
const FORMAT_OPTIONS: readonly string[] = ['PDF', 'DOCX', 'XLSX', 'PPTX', 'TXT']

/** top_k는 UI에서 노출하지 않고 서버 기본(10)을 그대로 쓴다. */
const TOP_K_DEFAULT = 10

const inputClass =
  'h-10 rounded-lg border border-[#d1d5db] bg-white px-3 text-sm text-[#111827] outline-none focus:border-[#1d4ed8] focus:ring-1 focus:ring-[#1d4ed8]'

interface PersistedSearchState {
  query: string
  source: SourceType | ''
  dateFrom: string
  dateTo: string
  docType: string
  status: SearchStatus
  hits: SearchHit[]
  errorMessage: string
  facets: SourceFacets | null
}

const PERSIST_KEY = 'docux:search-page'

const DEFAULT_PERSISTED: PersistedSearchState = {
  query: '',
  source: '',
  dateFrom: '',
  dateTo: '',
  docType: '',
  status: 'idle',
  hits: [],
  errorMessage: '',
  facets: null,
}

export function SearchPage({ client }: { client: ApiClient }) {
  // 라우트 전환 시 unmount해도 결과·필터 유지 (sessionStorage 하이드레이션).
  const initial = loadPersisted<PersistedSearchState>(PERSIST_KEY, DEFAULT_PERSISTED)
  const [query, setQuery] = useState(initial.query)
  const [source, setSource] = useState<SourceType | ''>(initial.source)
  const [dateFrom, setDateFrom] = useState(initial.dateFrom)
  const [dateTo, setDateTo] = useState(initial.dateTo)
  const [docType, setDocType] = useState(initial.docType)
  const [status, setStatus] = useState<SearchStatus>(
    // loading 상태는 hydrate 후 idle로 — 진행 중 요청이 끊겼을 수 있음.
    initial.status === 'loading' ? 'idle' : initial.status,
  )
  const [hits, setHits] = useState<SearchHit[]>(initial.hits)
  const [errorMessage, setErrorMessage] = useState(initial.errorMessage)
  // 검색어 기반 소스별 카운트(권한 인지). 검색 성공 후 갱신, 실패면 null.
  const [facets, setFacets] = useState<SourceFacets | null>(initial.facets)

  // 모든 상태 변경마다 sessionStorage 영속 — 탭 전환·새로고침 모두 살아남음.
  useEffect(() => {
    savePersisted<PersistedSearchState>(PERSIST_KEY, {
      query, source, dateFrom, dateTo, docType, status, hits, errorMessage, facets,
    })
  }, [query, source, dateFrom, dateTo, docType, status, hits, errorMessage, facets])

  // 응답 경합 가드: 늦게 도착한 이전 요청이 최신 결과를 덮어쓰지 않게 한다.
  const requestIdRef = useRef(0)

  // 검색 코어 — 폼 제출/추천 칩/facets 알약 클릭이 공유한다(중복 로직 금지).
  // `sourceOverride`가 있으면 그 값을 쓴다(state 비동기 갱신을 기다리지 않고 즉시 반영).
  async function performSearch(text: string, sourceOverride?: SourceType | '') {
    const trimmed = text.trim()
    if (!trimmed) {
      return
    }
    const effectiveSource = sourceOverride !== undefined ? sourceOverride : source
    const requestId = ++requestIdRef.current
    setStatus('loading')
    setFacets(null)
    try {
      const [results, facetsResp] = await Promise.all([
        client.search(trimmed, {
          source: effectiveSource || undefined,
          topK: TOP_K_DEFAULT,
          docType: docType || undefined,
          dateFrom: dateFrom || undefined,
          dateTo: dateTo || undefined,
        }),
        // facets는 source 필터와 무관하게 전체 분포를 보여준다(다른 소스로 빠르게 이동).
        client.facets(trimmed).catch(() => null),
      ])
      if (requestId !== requestIdRef.current) {
        return
      }
      setHits(results)
      setFacets(facetsResp)
      setStatus('done')
    } catch (error) {
      if (requestId !== requestIdRef.current) {
        return
      }
      setErrorMessage(toErrorMessage(error))
      setStatus('error')
    }
  }

  // facets 알약 클릭 — source 필터 즉시 적용 + 재검색.
  function handleFacetClick(nextSource: SourceType | '') {
    setSource(nextSource)
    void performSearch(query, nextSource)
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void performSearch(query)
  }

  // 결과 화면의 "통합 검색" 제목 클릭 → 초기(idle) 화면으로 복귀(검색어·결과 초기화).
  function resetToInitial() {
    requestIdRef.current++ // 진행 중 요청이 복귀 후 결과를 덮어쓰지 않게 무효화.
    setQuery('')
    setHits([])
    setFacets(null)
    setErrorMessage('')
    setStatus('idle')
  }

  // 소스·날짜·포맷 필터 — 두 화면이 공유하는 공통 조각.
  const filterControls = (
    <>
      <label className="flex flex-col gap-1">
        <span className="text-xs font-semibold text-[#6b7280]">소스</span>
        <select
          name="source"
          aria-label="소스"
          value={source}
          onChange={(event) => setSource(event.target.value as SourceType | '')}
          className={inputClass}
        >
          <option value="">전체</option>
          {SOURCE_OPTIONS.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-xs font-semibold text-[#6b7280]">시작일</span>
        <input
          type="date"
          name="dateFrom"
          aria-label="시작일"
          value={dateFrom}
          max={dateTo || undefined}
          onChange={(e) => setDateFrom(e.target.value)}
          className={inputClass}
        />
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-xs font-semibold text-[#6b7280]">종료일</span>
        <input
          type="date"
          name="dateTo"
          aria-label="종료일"
          value={dateTo}
          min={dateFrom || undefined}
          onChange={(e) => setDateTo(e.target.value)}
          className={inputClass}
        />
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-xs font-semibold text-[#6b7280]">포맷</span>
        <select
          name="docType"
          aria-label="포맷"
          value={docType}
          onChange={(event) => setDocType(event.target.value)}
          className={inputClass}
        >
          <option value="">전체</option>
          {FORMAT_OPTIONS.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      </label>
    </>
  )

  // 초기 화면 — design/search_init.html 히어로.
  if (status === 'idle') {
    return (
      <section className="relative flex h-full flex-col">
        <form
          onSubmit={handleSubmit}
          aria-label="검색"
          className="flex flex-1 flex-col items-center justify-center px-8 pb-20"
        >
          {/* 상단 아이콘 */}
          <div className="mb-6 flex h-16 w-16 items-center justify-center rounded-2xl border border-[#e5e7eb] bg-white shadow-sm">
            <SearchGlyph className="text-[#374151]" size={32} />
          </div>

          {/* 헤딩 + 안내 */}
          <h2 className="mb-3 text-3xl font-bold tracking-tight text-[#111827]">
            통합 검색
          </h2>
          <p className="mb-10 max-w-md text-center text-[15px] text-[#6b7280]">
            사내 지식베이스 전체를 하나의 통합 검색으로 탐색하세요.
          </p>

          {/* 큰 검색 바 */}
          <div className="relative mb-6 w-full max-w-[800px]">
            <SearchGlyph className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-[#9ca3af]" />
            <input
              type="search"
              name="query"
              aria-label="검색어"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="검색어를 입력하세요"
              className="h-14 w-full rounded-xl border border-[#e5e7eb] bg-white pl-12 pr-28 text-[15px] text-[#111827] shadow-[0_4px_12px_rgba(0,0,0,0.05)] outline-none focus:border-[#1d4ed8] focus:ring-1 focus:ring-[#1d4ed8]"
            />
            <button
              type="submit"
              className="absolute right-2 top-1/2 h-10 -translate-y-1/2 cursor-pointer rounded-lg bg-[#1d4ed8] px-6 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-[#1e40af]"
            >
              검색
            </button>
          </div>

          {/* 소스·개수 필터 */}
          <div className="mb-12 flex flex-wrap items-end justify-center gap-4">
            {filterControls}
          </div>

        </form>

        {/* 시스템 상태 푸터 */}
        <div className="absolute bottom-6 flex w-full items-center justify-center gap-8 text-[11px] font-bold uppercase tracking-widest text-[#9ca3af]">
          <StatusDot label="인덱스 라이브" />
          <StatusDot label="권한 적용됨" />
        </div>
      </section>
    )
  }

  // 검색 후 — design/search_result.html (상단 검색 바 + 필터 + 결과).
  return (
    <section className="flex h-full flex-col">
      <form onSubmit={handleSubmit} aria-label="검색">
        <div className="border-b border-[#e5e7eb] px-8 pb-5 pt-7">
          <h2 className="mb-4 text-2xl font-bold tracking-tight text-[#111827]">
            <button
              type="button"
              onClick={resetToInitial}
              title="통합 검색 초기 화면으로"
              aria-label="통합 검색 초기 화면으로 이동"
              className="cursor-pointer rounded text-[#111827] transition-colors hover:text-[#1d4ed8] focus:outline-none focus-visible:ring-2 focus-visible:ring-[#1d4ed8]"
            >
              통합 검색
            </button>
          </h2>
          <div className="relative">
            <SearchGlyph className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-[#9ca3af]" />
            <input
              type="search"
              name="query"
              aria-label="검색어"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="검색어를 입력하세요"
              className="h-12 w-full rounded-lg border border-[#d1d5db] bg-white pl-12 pr-28 text-[15px] text-[#111827] shadow-sm outline-none focus:border-[#1d4ed8] focus:ring-1 focus:ring-[#1d4ed8]"
            />
            <button
              type="submit"
              className="absolute right-2 top-1/2 h-9 -translate-y-1/2 cursor-pointer rounded-lg bg-[#1d4ed8] px-5 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-[#1e40af]"
            >
              검색
            </button>
          </div>
        </div>

        <div className="flex flex-wrap items-end gap-4 border-b border-[#e5e7eb] px-8 py-4">
          <span className="self-center text-sm font-semibold text-[#374151]">
            필터
          </span>
          {filterControls}
          {facets ? (
            <div
              aria-label="검색 결과 분포"
              className="ml-auto flex flex-wrap items-center gap-2 text-xs"
            >
              <button
                type="button"
                onClick={() => handleFacetClick('')}
                aria-pressed={source === ''}
                className={`cursor-pointer rounded-full px-3 py-1 font-semibold transition-colors ${
                  source === ''
                    ? 'bg-[#1d4ed8] text-white'
                    : 'bg-[#eef2ff] text-[#1d4ed8] hover:bg-[#e0e7ff]'
                }`}
              >
                전체 {facets.total}
              </button>
              {Object.entries(facets.by_source).map(([src, count]) => {
                const active = source === src
                return (
                  <button
                    key={src}
                    type="button"
                    onClick={() => handleFacetClick(src as SourceType)}
                    aria-pressed={active}
                    className={`cursor-pointer rounded-full px-3 py-1 font-medium transition-colors ${
                      active
                        ? 'bg-[#1d4ed8] text-white'
                        : 'border border-[#e5e7eb] bg-white text-[#374151] hover:bg-[#f3f4f6]'
                    }`}
                  >
                    {SOURCE_LABEL[src] ?? src} {count}
                  </button>
                )
              })}
            </div>
          ) : null}
        </div>
      </form>

      <div className="flex-1 overflow-y-auto px-8 py-6">
        <SearchResults
          status={status}
          hits={hits}
          errorMessage={errorMessage}
          client={client}
          onDeleted={(docId) => setHits((prev) => prev.filter((h) => h.doc_id !== docId))}
        />
      </div>
    </section>
  )
}

/** 시스템 상태 점 — 초록 점 + 라벨. */
function StatusDot({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-2">
      <div className="h-2 w-2 rounded-full bg-[#22c55e]" />
      <span>{label}</span>
    </div>
  )
}

/** 검색 입력 좌측 돋보기 글리프 (design/search_*.html). */
function SearchGlyph({
  className,
  size = 20,
}: {
  className?: string
  size?: number
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      aria-hidden
      className={className}
    >
      <circle cx="11" cy="11" r="8" />
      <line x1="21" y1="21" x2="16.65" y2="16.65" />
    </svg>
  )
}

/** 에러를 사용자 메시지로 변환 — ApiError는 메시지를(토큰 미포함), 그 외는 일반 안내. */
function toErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.message
  }
  return '검색 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.'
}
