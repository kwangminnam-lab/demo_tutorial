/**
 * 문서 비교 페이지.
 *
 * 두 문서 식별자(doc_id) 입력 → `client.diff` 호출 → `DiffResult` 시각화.
 * 라인 단위 add/delete/change를 색으로 구분하고, 변경 라인 안의 바뀐 단어
 * (`WordSpan.changed`)는 인라인 하이라이트한다. 상단에 추가/삭제/변경 카운트 요약.
 *
 * 디자인은 design/document_init·document_result.html 기준 — 흰 업로드 카드 + 파랑/빨강 diff.
 *
 * 현 단계 백엔드는 `document_resolver`가 미주입일 수 있어 `/v1/diff`가 에러를 낼 수
 * 있다(ADR-008·deps). 그 경우 앱을 죽이지 않고 graceful 메시지로 표시하며, 입력 UI는
 * 계속 동작한다. diff 로직(추출·라인/단어 비교·권한)은 서버 권위다(프론트 재구현 금지).
 *
 * `client`는 주입받는다(DI): 테스트는 모킹 클라이언트를, 실 배선(라우팅 step)은
 * `createClient(() => token)`을 넘긴다. 페이지는 데이터 소스에 비결합·순수하다.
 */

import {
  useRef,
  useState,
  useSyncExternalStore,
  type ChangeEvent,
  type DragEvent,
  type FormEvent,
} from 'react'
import type { ApiClient } from '../api/client'
import type { DiffOp, DiffResult, WordSpan } from '../api/types'
import { CircularProgress, useTimedProgress } from '../app/CircularProgress'
import { diffStore } from '../lib/diffStore'

type DiffStatus = 'idle' | 'loading' | 'error' | 'done'

// 라인 셀 공통 스타일 — 행간 정렬을 위해 좌우 셀이 같은 패딩/폰트를 쓴다.
// 한국어 본문은 비단절 공백(U+00A0)이 섞여 자동 줄바꿈이 막히므로 wordBreak
// break-word + overflowWrap anywhere로 강제 줄바꿈한다. 추가로 텍스트 렌더 시
// NBSP를 일반 공백으로 정규화(`normalizeForWrap`)해 인접 셀로의 오버플로우를 막는다.
const cellBaseStyle: React.CSSProperties = {
  margin: 0,
  padding: '0.2rem 0.75rem',
  whiteSpace: 'pre-wrap',
  overflowWrap: 'anywhere',
  wordBreak: 'break-word',
  fontFamily:
    '-apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo", "Malgun Gothic", "Segoe UI", Roboto, sans-serif',
  fontSize: '0.95rem',
  lineHeight: 1.7,
  color: '#1f2937',
  verticalAlign: 'top',
  borderRadius: '0',
}

/** 디스플레이용 정규화: NBSP(U+00A0) → 일반 공백(줄바꿈 가능). 원본 의미는 동일. */
function normalizeForWrap(text: string | null | undefined): string {
  return (text ?? '').replace(/\u00A0/g, ' ')
}

// \uCD94\uCD9C\uAE30\uAC00 \uBCF8\uBB38\uC5D0 \uBC15\uB294 \uB9C8\uCEE4 \uB77C\uC778 \u2014 \uADF8\uB9BC/\uD45C \uBCC0\uACBD\uC744 \uC0AC\uC6A9\uC790\uC5D0\uAC8C \uAC00\uC2DC\uD654\uD558\uAE30 \uC704\uD55C \uD328\uD134.
const IMAGE_MARKER_RE =
  /^\[IMAGE(?:\s+p=(\d+))?(?:\s+#(\d+))?\s+sha=([0-9a-f]+|unknown)(?:\s+x(\d+))?\]$/
const TABLE_MARKER_RE = /^\[TABLE\s+r=(\d+)\s+c=(\d+)\]$/

const markerLabelStyle: React.CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: '0.25rem',
  padding: '0.1rem 0.45rem',
  borderRadius: '0.25rem',
  fontSize: '0.78rem',
  fontWeight: 600,
  fontFamily:
    '-apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo", sans-serif',
  background: '#dbeafe',
  color: '#1d4ed8',
}

/** \uB9C8\uCEE4 \uB77C\uC778\uC774\uBA74 \uC0AC\uB78C\uC774 \uC77D\uAE30 \uC88B\uC740 \uBB38\uC790\uC5F4 \uB77C\uBCA8\uC744 \uBC18\uD658, \uC544\uB2C8\uBA74 null. */
function markerLabel(text: string | null | undefined): string | null {
  const raw = normalizeForWrap(text).trim()
  const image = IMAGE_MARKER_RE.exec(raw)
  if (image) {
    const [, page, idx, sha, count] = image
    const detail = [
      page ? `p.${page}` : null,
      idx ? `#${idx}` : null,
      `sha=${sha}`,
      count ? `\u00D7${count}` : null,
    ]
      .filter(Boolean)
      .join(' \u00B7 ')
    return `\uD83D\uDDBC \uADF8\uB9BC (${detail})`
  }
  const table = TABLE_MARKER_RE.exec(raw)
  if (table) {
    const [, rows, cols] = table
    return `\u25A6 \uD45C (${rows}\u00D7${cols})`
  }
  return null
}

// \uD45C \uD589: `| \uC140 | \uC140 |` \u2014 markdown style row. \uCCAB\u00B7\uB05D \uBE48 \uC140 \uC81C\uAC70 \uD6C4 trim.
const TABLE_ROW_RE = /^\|.*\|$/
function splitTableRow(text: string): string[] {
  const parts = text.trim().split('|')
  // \uC55E\uB4A4 \uBE48 \uD1A0\uD070 \uC81C\uAC70
  if (parts.length >= 2 && parts[0] === '') parts.shift()
  if (parts.length >= 1 && parts[parts.length - 1] === '') parts.pop()
  return parts.map((c) => c.trim())
}

// \uD45C \uC140(\uBBF8\uB2C8 \uD14C\uC774\uBE14) \uC2A4\uD0C0\uC77C.
const tableCellStyle: React.CSSProperties = {
  border: '1px solid #d1d5db',
  padding: '0.3rem 0.55rem',
  fontSize: '0.82rem',
  background: '#ffffff',
  verticalAlign: 'top',
}

const tableCaptionStyle: React.CSSProperties = {
  ...markerLabelStyle,
  background: '#f1f5f9',
  color: '#475569',
}

const imageThumbStyle: React.CSSProperties = {
  maxWidth: '100%',
  maxHeight: '200px',
  borderRadius: '0.375rem',
  border: '1px solid #d1d5db',
  background: '#ffffff',
  display: 'block',
}

/**
 * \uB9C8\uCEE4\u00B7\uD45C \uD589\uC744 \uC2E4\uC81C \uD45C\uD604\uC73C\uB85C \uB80C\uB354\uD558\uACE0, \uADF8 \uC678\uB294 \uD3C9\uBB38 \uADF8\uB300\uB85C.
 *
 * - `[IMAGE sha=...]`: image_blobs[sha] data URL\uC774 \uC788\uC73C\uBA74 <img>, \uC5C6\uC73C\uBA74 \uB77C\uBCA8\uB85C.
 * - `[TABLE r=N c=M]`: \uC791\uC740 \uCEA1\uC158(\uD45C N\u00D7M).
 * - `| \uC140 | \uC140 |`: <table> \uD55C \uD589\uC73C\uB85C \uC140 \uACA9\uC790 \uD45C\uC2DC.
 */
function MarkerOrText({
  text,
  imageBlobs,
}: {
  text: string | null | undefined
  imageBlobs?: Record<string, string>
}) {
  const raw = normalizeForWrap(text).trim()
  const image = IMAGE_MARKER_RE.exec(raw)
  if (image) {
    const [, page, idx, sha, count] = image
    const detail = [
      page ? `p.${page}` : null,
      idx ? `#${idx}` : null,
      count ? `\u00D7${count}` : null,
    ]
      .filter(Boolean)
      .join(' \u00B7 ')
    const src = imageBlobs?.[sha]
    if (src) {
      return (
        <figure style={{ margin: 0 }}>
          <img src={src} alt={`\uADF8\uB9BC ${detail}`} style={imageThumbStyle} />
          {detail ? (
            <figcaption style={{ marginTop: '0.25rem', fontSize: '0.72rem', color: '#6b7280' }}>
              {detail}
            </figcaption>
          ) : null}
        </figure>
      )
    }
    return (
      <span style={markerLabelStyle}>
        \uD83D\uDDBC \uADF8\uB9BC{detail ? ` (${detail})` : ''}
      </span>
    )
  }
  const table = TABLE_MARKER_RE.exec(raw)
  if (table) {
    const [, rows, cols] = table
    return (
      <span style={tableCaptionStyle}>
        \u25A6 \uD45C ({rows}\u00D7{cols})
      </span>
    )
  }
  if (TABLE_ROW_RE.test(raw)) {
    const cells = splitTableRow(raw)
    return (
      <table style={{ borderCollapse: 'collapse', width: '100%' }}>
        <tbody>
          <tr>
            {cells.map((c, i) => (
              <td key={i} style={tableCellStyle}>
                {c}
              </td>
            ))}
          </tr>
        </tbody>
      </table>
    )
  }
  return <>{normalizeForWrap(text)}</>
}

// 좌(Original) 셀: equal=무색 / delete=옅은 빨강 strikethrough / change=옅은 빨강 / add 행=빈 셀.
const leftCellStyleByOp: Record<DiffOp['op'], React.CSSProperties> = {
  equal: cellBaseStyle,
  add: { ...cellBaseStyle, backgroundColor: 'transparent' },
  delete: {
    ...cellBaseStyle,
    backgroundColor: '#fef2f2',
    color: '#991b1b',
    textDecoration: 'line-through',
  },
  change: { ...cellBaseStyle, backgroundColor: '#fef2f2', color: '#1f2937' },
}

// 우(Revised) 셀: equal=무색 / add=옅은 초록 / change=옅은 초록 / delete 행=빈 셀.
const rightCellStyleByOp: Record<DiffOp['op'], React.CSSProperties> = {
  equal: cellBaseStyle,
  add: { ...cellBaseStyle, backgroundColor: '#f0fdf4', color: '#166534' },
  delete: { ...cellBaseStyle, backgroundColor: 'transparent' },
  change: { ...cellBaseStyle, backgroundColor: '#f0fdf4', color: '#1f2937' },
}

// 변경 행 내부 — 바뀐 단어 강조. fontWeight 600은 테스트가 잠그는 계약(클래스로 바꾸지 않음).
const changedWordStyle: React.CSSProperties = {
  fontWeight: 600,
  textDecoration: 'underline',
  textDecorationThickness: '2px',
  textUnderlineOffset: '3px',
}

export function DiffPage({ client }: { client: ApiClient }) {
  // 모든 비교 상태는 모듈 싱글톤 store에 — 탭 이동 시 unmount 돼도 fetch 계속 진행.
  // remount 시 useSyncExternalStore로 같은 store를 다시 구독해 결과를 그대로 표시.
  const storeState = useSyncExternalStore(diffStore.subscribe, diffStore.getSnapshot)
  const { fileA, fileB, status, result, errorMessage } = storeState

  const setFileA = (f: File | null) => diffStore.setFileA(f)
  const setFileB = (f: File | null) => diffStore.setFileB(f)

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!fileA || !fileB) {
      return
    }
    void diffStore.compare(client, fileA, fileB)
  }

  // 결과 화면 → 입력 화면 복귀(파일 초기화 — 빈 폼으로 새로 시작).
  function handleBack() {
    diffStore.reset()
  }

  // 결과 화면(status==='done'): 입력 폼 숨기고 3컬럼 결과 + 뒤로가기만. 화면 폭 풀로.
  if (status === 'done' && result !== null) {
    return (
      <section className="w-full px-6 py-8">
        <div className="mb-6 flex items-center justify-between gap-4">
          <button
            type="button"
            onClick={handleBack}
            aria-label="뒤로 가기"
            className="flex cursor-pointer items-center gap-2 rounded-lg border border-[#d1d5db] bg-white px-4 py-2 text-sm font-semibold text-[#374151] shadow-sm transition-colors hover:bg-[#f3f4f6]"
          >
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden
            >
              <line x1="19" y1="12" x2="5" y2="12" />
              <polyline points="12 19 5 12 12 5" />
            </svg>
            뒤로
          </button>
          <h2 className="text-2xl font-bold tracking-tight text-[#111827]">
            문서 비교 결과
          </h2>
          <span className="w-[88px]" aria-hidden />
        </div>
        <DiffView
          status={status}
          result={result}
          errorMessage={errorMessage}
          leftLabel={fileA?.name || '기존 문서'}
          rightLabel={fileB?.name || '수정 문서'}
        />
      </section>
    )
  }

  // 입력 화면(idle/loading/error): 폼 + 상태 메시지.
  return (
    <section className="mx-auto w-full max-w-5xl px-8 py-10">
      <h2 className="text-3xl font-bold tracking-tight text-[#111827]">
        문서 비교
      </h2>
      <p className="mt-2 text-[15px] text-[#6b7280]">
        두 문서의 추가·삭제·변경을 라인 단위로 비교합니다.
      </p>

      <form onSubmit={handleSubmit} aria-label="문서 비교" className="mt-8">
        <div className="flex flex-col gap-6 sm:flex-row">
          <DocDropField
            label="기존 문서"
            file={fileA}
            onFile={setFileA}
          />
          <DocDropField
            label="수정 문서"
            file={fileB}
            onFile={setFileB}
          />
        </div>
        <div className="mt-5 flex items-center justify-between gap-3">
          <p className="text-xs text-[#9ca3af]">
            두 파일을 끌어다 놓거나 선택해 비교하세요(서버에 영속 저장되지 않음).
          </p>
          <button
            type="submit"
            disabled={fileA === null || fileB === null}
            className="h-10 shrink-0 cursor-pointer rounded-lg bg-[#1d4ed8] px-6 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-[#1e40af] disabled:cursor-not-allowed disabled:opacity-40"
          >
            비교
          </button>
        </div>
      </form>

      <div className="mt-6">
        <DiffView
          status={status}
          result={result}
          errorMessage={errorMessage}
          leftLabel={fileA?.name || '기존 문서'}
          rightLabel={fileB?.name || '수정 문서'}
        />
      </div>
    </section>
  )
}

interface DiffViewProps {
  status: DiffStatus
  result: DiffResult | null
  errorMessage: string
  leftLabel: string
  rightLabel: string
}

/**
 * 상태별 diff 렌더 — design/document_result.html 3컬럼(Original / Revised / Summary).
 *
 *  - 좌(Original): equal·delete·change 행에 원본 텍스트 표시(delete 빨강 strikethrough).
 *  - 우(Revised): equal·add·change 행에 개정 텍스트 표시(add 초록).
 *  - change 행은 좌·우 셀이 하나의 `data-op="change"` 컨테이너에 들어가, 좌·우의
 *    바뀐 단어가 모두 `fontWeight: 600` span으로 강조된다(테스트 계약).
 *  - 우측 사이드바: 총 변경·유사도·추가/삭제 막대 카드(aria-label "변경 요약").
 *
 * 에러는 graceful 메시지(앱을 죽이지 않는다, ADR-008).
 */
/**
 * 비교 로딩 패널 — 원형 progress + 단계 캡션.
 * Diff는 양쪽 파일 추출 + 라인/단어 비교 + 페이지 PNG 렌더 → 평균 ~20초.
 */
function DiffLoadingPanel({
  leftLabel,
  rightLabel,
}: {
  leftLabel: string | null
  rightLabel: string | null
}) {
  const estimatedMs = 20_000
  const progress = useTimedProgress(true, estimatedMs)
  const remainingSec = Math.max(
    0,
    Math.round((estimatedMs * (100 - progress)) / 100 / 1000),
  )
  return (
    <div
      aria-live="polite"
      className="flex w-full flex-col items-center justify-center gap-5 py-12"
    >
      <CircularProgress
        value={progress}
        size={140}
        thickness={10}
        label={`${Math.floor(progress)}%`}
        caption="Diff"
        ariaLabel="비교 진행률"
      />
      <div className="text-center">
        <p className="text-sm font-semibold text-[#111827]">
          {leftLabel ?? '문서 A'} ↔ {rightLabel ?? '문서 B'}
        </p>
        <p className="mt-1 text-xs text-[#6b7280]">
          비교 중 · 남은 시간 약 {remainingSec}초
        </p>
        <p className="mt-1 text-[11px] text-[#9ca3af]">
          (추출 → 라인/단어 비교 → 페이지 PNG 렌더)
        </p>
      </div>
    </div>
  )
}

function DiffView({ status, result, errorMessage, leftLabel, rightLabel }: DiffViewProps) {
  if (status === 'idle') {
    return null
  }
  if (status === 'loading') {
    return <DiffLoadingPanel leftLabel={leftLabel} rightLabel={rightLabel} />
  }
  if (status === 'error') {
    return (
      <p role="alert" className="text-sm font-medium text-[#dc2626]">
        {errorMessage}
      </p>
    )
  }
  if (result === null) {
    return null
  }

  const totalChanges = result.added + result.deleted + result.changed
  const totalOps = result.ops.length || 1
  const similarity = Math.round(((totalOps - totalChanges) / totalOps) * 100)
  const maxBar = Math.max(result.added, result.deleted, 1)
  const addPct = Math.round((result.added / maxBar) * 100)
  const delPct = Math.round((result.deleted / maxBar) * 100)

  // Major Discrepancies — 변경 유의 ops 상위 N건을 본문 미리보기와 함께.
  const highlights: { kind: '추가' | '삭제' | '변경'; left?: string | null; right?: string | null }[] = []
  for (const op of result.ops) {
    if (highlights.length >= 4) break
    if (op.op === 'add') highlights.push({ kind: '추가', right: op.right })
    else if (op.op === 'delete') highlights.push({ kind: '삭제', left: op.left })
    else if (op.op === 'change') highlights.push({ kind: '변경', left: op.left, right: op.right })
  }

  const previewsA = result.page_previews_a ?? {}
  const previewsB = result.page_previews_b ?? {}
  const hasPagePreviews =
    Object.keys(previewsA).length > 0 || Object.keys(previewsB).length > 0

  // 뷰 모드 — 페이지 프리뷰가 1차 사용 경험(원본 형태로 직접 비교). 텍스트 diff는 전환.
  // 페이지 프리뷰 부재(미지원 포맷) 시에는 토글 숨기고 text 모드로 강제.
  const [viewMode, setViewMode] = useState<'pages' | 'text'>('pages')
  const effectiveMode: 'pages' | 'text' = hasPagePreviews ? viewMode : 'text'

  return (
    <div className="flex w-full flex-col gap-6 xl:flex-row">
      {/* 좌측: 토글 + 본문 (페이지 프리뷰 또는 텍스트 diff) */}
      <div className="flex min-w-0 flex-1 flex-col gap-3">
        {hasPagePreviews ? (
          <ViewModeTabs mode={effectiveMode} onChange={setViewMode} />
        ) : null}
        {effectiveMode === 'pages' ? (
          <PagePreviewPanel
            leftLabel={leftLabel}
            rightLabel={rightLabel}
            previewsA={previewsA}
            previewsB={previewsB}
          />
        ) : (
          <TextDiffPanel
            ops={result.ops}
            imageBlobs={result.image_blobs}
            leftLabel={leftLabel}
            rightLabel={rightLabel}
          />
        )}
      </div>

      {/* 우측 요약 사이드바 — 컴팩트(좁고 작은 글자) */}
      <aside className="flex w-full shrink-0 flex-col gap-3 xl:w-[13rem]">
        <div
          aria-label="변경 요약"
          className="rounded-md border border-gray-200 bg-white p-3 shadow-sm"
        >
          <h2 className="mb-2 text-[11px] font-bold uppercase tracking-wide text-gray-700">
            비교 요약
          </h2>
          <div className="mb-3 flex gap-2 border-b border-gray-100 pb-2">
            <div className="flex-1">
              <p className="text-[10px] font-semibold uppercase tracking-wide text-gray-500">총 변경</p>
              <p className="mt-0.5 text-lg font-bold text-blue-700">{totalChanges}</p>
            </div>
            <div className="flex-1">
              <p className="text-[10px] font-semibold uppercase tracking-wide text-gray-500">유사도</p>
              <p className="mt-0.5 text-lg font-bold text-blue-700">{similarity}%</p>
            </div>
          </div>
          <div className="space-y-2 text-[11px]">
            <div>
              <div className="mb-0.5 flex justify-between">
                <span className="text-gray-600">추가 {result.added}</span>
                <span className="font-bold text-green-600">+{result.added}</span>
              </div>
              <div className="h-1 w-full rounded-full bg-gray-200">
                <div className="h-1 rounded-full bg-green-500" style={{ width: `${addPct}%` }} />
              </div>
            </div>
            <div>
              <div className="mb-0.5 flex justify-between">
                <span className="text-gray-600">삭제 {result.deleted}</span>
                <span className="font-bold text-red-500">-{result.deleted}</span>
              </div>
              <div className="h-1 w-full rounded-full bg-gray-200">
                <div className="h-1 rounded-full bg-red-500" style={{ width: `${delPct}%` }} />
              </div>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-600">변경 {result.changed}</span>
              <span className="font-bold text-amber-600">~{result.changed}</span>
            </div>
          </div>
        </div>

        {/* 관련 내용 — 상위 4건만, 컴팩트 */}
        <div className="overflow-hidden rounded-md border border-gray-200 bg-white shadow-sm">
          <div className="flex items-center justify-between border-b border-gray-200 bg-gray-50 px-3 py-2">
            <h2 className="text-[11px] font-bold uppercase tracking-wide text-gray-900">관련 내용</h2>
            {totalChanges > highlights.length ? (
              <span className="rounded-full bg-blue-50 px-1.5 py-0.5 text-[9px] font-bold text-blue-700">
                {highlights.length}/{totalChanges}
              </span>
            ) : null}
          </div>
          {highlights.length === 0 ? (
            <p className="p-3 text-center text-[11px] text-gray-400">동일합니다.</p>
          ) : (
            <div className="divide-y divide-gray-100">
              {highlights.map((h, idx) => (
                <div key={idx} className="p-2">
                  <span
                    className={`inline-block rounded px-1.5 py-0 text-[9px] font-bold ${
                      h.kind === '추가'
                        ? 'bg-green-100 text-green-700'
                        : h.kind === '삭제'
                          ? 'bg-red-100 text-red-700'
                          : 'bg-amber-100 text-amber-700'
                    }`}
                  >
                    {h.kind}
                  </span>
                  {h.left ? (
                    <p className="mt-1 break-words text-[11px] leading-tight text-red-700 line-through">
                      {markerLabel(h.left) ?? truncate(h.left, 80)}
                    </p>
                  ) : null}
                  {h.right ? (
                    <p className="mt-0.5 break-words text-[11px] leading-tight text-green-700">
                      {markerLabel(h.right) ?? truncate(h.right, 80)}
                    </p>
                  ) : null}
                </div>
              ))}
            </div>
          )}
        </div>
      </aside>
    </div>
  )
}

/**
 * 뷰 모드 탭 — "원본 페이지 프리뷰" vs "텍스트 diff".
 * 페이지 프리뷰가 1차 사용 경험 (사용자가 원본 형태로 직접 비교).
 */
function ViewModeTabs({
  mode,
  onChange,
}: {
  mode: 'pages' | 'text'
  onChange: (m: 'pages' | 'text') => void
}) {
  const baseTab =
    'rounded-md px-4 py-2 text-sm font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-blue-300'
  return (
    <div
      role="tablist"
      aria-label="비교 뷰 모드"
      className="inline-flex w-fit gap-1 rounded-lg border border-gray-200 bg-gray-50 p-1"
    >
      <button
        type="button"
        role="tab"
        aria-selected={mode === 'pages'}
        onClick={() => onChange('pages')}
        className={`${baseTab} ${
          mode === 'pages'
            ? 'bg-white text-blue-800 shadow'
            : 'text-gray-600 hover:text-gray-900'
        }`}
      >
        원본 페이지 프리뷰
      </button>
      <button
        type="button"
        role="tab"
        aria-selected={mode === 'text'}
        onClick={() => onChange('text')}
        className={`${baseTab} ${
          mode === 'text'
            ? 'bg-white text-blue-800 shadow'
            : 'text-gray-600 hover:text-gray-900'
        }`}
      >
        텍스트 diff
      </button>
    </div>
  )
}

/**
 * 텍스트 diff 본문 패널 — 좌·우 컬럼 헤더 + 라인 단위 비교 테이블.
 * (기존 DiffView의 본문 블록을 그대로 추출 — 토글로 뷰 분기하기 위함.)
 */
function TextDiffPanel({
  ops,
  imageBlobs,
  leftLabel,
  rightLabel,
}: {
  ops: DiffOp[]
  imageBlobs: Record<string, string> | undefined
  leftLabel: string
  rightLabel: string
}) {
  return (
    <div className="flex flex-col">
      <div className="grid grid-cols-2 gap-3">
        <div className="flex min-w-0 items-center justify-between gap-2 rounded-t-lg border border-indigo-200 bg-indigo-50 px-4 py-3 text-sm font-semibold text-indigo-900">
          <span className="min-w-0 truncate" title={leftLabel}>{leftLabel}</span>
          <span className="shrink-0 rounded bg-indigo-100 px-2 py-1 text-[10px] font-bold tracking-wider text-indigo-800">
            ORIGINAL
          </span>
        </div>
        <div className="flex min-w-0 items-center justify-between gap-2 rounded-t-lg border border-blue-800 bg-blue-700 px-4 py-3 text-sm font-semibold text-white">
          <span className="min-w-0 truncate" title={rightLabel}>{rightLabel}</span>
          <span className="shrink-0 rounded bg-blue-600 px-2 py-1 text-[10px] font-bold tracking-wider text-white">
            REVISED
          </span>
        </div>
      </div>
      <div
        aria-label="비교 결과"
        className="max-h-[86vh] overflow-y-auto rounded-b-lg border border-t-0 border-gray-200 bg-white p-6 shadow-sm"
      >
        <table
          className="w-full border-separate"
          style={{ tableLayout: 'fixed', borderSpacing: '0.25rem 0' }}
        >
          <colgroup>
            <col style={{ width: '50%' }} />
            <col style={{ width: '50%' }} />
          </colgroup>
          <tbody>
            {ops.map((op, index) => (
              <DiffRow key={index} op={op} imageBlobs={imageBlobs} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

/**
 * 원본 페이지 프리뷰 패널 — 비교 화면의 주 사용 경험.
 *
 * 좌·우 컬럼이 **완전히 독립**으로 스크롤된다 — 양쪽 페이지 수·길이가 다를 때
 * 사용자가 자기 페이스로 비교 가능. 동기 스크롤 X.
 *
 * 마커(`[IMAGE]`·`[TABLE]`·`| 셀 |`)는 텍스트 diff(다른 탭)에 그대로 유지된다.
 */
function PagePreviewPanel({
  leftLabel,
  rightLabel,
  previewsA,
  previewsB,
}: {
  leftLabel: string
  rightLabel: string
  previewsA: Record<string, string>
  previewsB: Record<string, string>
}) {
  const pagesA = sortedPageNumbers(previewsA)
  const pagesB = sortedPageNumbers(previewsB)

  return (
    <section
      aria-label="원본 페이지 프리뷰"
      className="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm"
    >
      <div className="grid grid-cols-2 gap-3 px-3 pt-3">
        <div className="flex min-w-0 items-center justify-between gap-2 rounded-t-lg border border-indigo-200 bg-indigo-50 px-4 py-3 text-sm font-semibold text-indigo-900">
          <span className="min-w-0 truncate" title={leftLabel}>
            {leftLabel} · {pagesA.length}p
          </span>
          <span className="shrink-0 rounded bg-indigo-100 px-2 py-1 text-[10px] font-bold tracking-wider text-indigo-800">
            ORIGINAL
          </span>
        </div>
        <div className="flex min-w-0 items-center justify-between gap-2 rounded-t-lg border border-blue-800 bg-blue-700 px-4 py-3 text-sm font-semibold text-white">
          <span className="min-w-0 truncate" title={rightLabel}>
            {rightLabel} · {pagesB.length}p
          </span>
          <span className="shrink-0 rounded bg-blue-600 px-2 py-1 text-[10px] font-bold tracking-wider text-white">
            REVISED
          </span>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-3 p-3">
        <div
          aria-label="원본 페이지 스크롤"
          className="max-h-[86vh] overflow-y-auto rounded-b-lg border border-t-0 border-indigo-200 bg-indigo-50/30 p-3"
        >
          <div className="flex flex-col gap-4">
            {pagesA.map((page) => (
              <PagePreview
                key={`a-${page}`}
                page={page}
                src={previewsA[String(page)]}
                tone="left"
              />
            ))}
          </div>
        </div>
        <div
          aria-label="변경 페이지 스크롤"
          className="max-h-[86vh] overflow-y-auto rounded-b-lg border border-t-0 border-blue-300 bg-blue-50/30 p-3"
        >
          <div className="flex flex-col gap-4">
            {pagesB.map((page) => (
              <PagePreview
                key={`b-${page}`}
                page={page}
                src={previewsB[String(page)]}
                tone="right"
              />
            ))}
          </div>
        </div>
      </div>
      <p className="border-t border-gray-100 bg-gray-50 px-4 py-2 text-[11px] text-gray-500">
        양쪽 컬럼 독립 스크롤 · 색칠: 추가(초록) · 삭제(빨강) · 변경(노랑) · 마커는
        텍스트 diff 탭에 그대로 유지
      </p>
    </section>
  )
}

/**
 * 한 페이지 프리뷰 카드 — 페이지 번호 배지 + 풀폭 PNG 이미지.
 *
 * 이미지가 없으면(한쪽 문서가 더 짧음) 같은 높이의 폴백 박스로 줄 정렬 유지.
 * 줄 정렬을 강제하지는 않음 (페이지마다 실제 높이가 다르므로 동기 스크롤은
 * 부모가 비율로 처리).
 */
function PagePreview({
  page,
  src,
  tone,
}: {
  page: number
  src: string | undefined
  tone: 'left' | 'right'
}) {
  const ringClass = tone === 'left' ? 'ring-indigo-300' : 'ring-blue-300'
  const badgeClass =
    tone === 'left'
      ? 'bg-indigo-100 text-indigo-800'
      : 'bg-blue-100 text-blue-800'
  return (
    <figure className="flex flex-col gap-1">
      <figcaption className="flex items-center gap-2">
        <span
          className={`rounded px-2 py-0.5 text-[10px] font-bold tracking-wider ${badgeClass}`}
        >
          PAGE {page}
        </span>
      </figcaption>
      {src ? (
        <img
          src={src}
          alt={`페이지 ${page}`}
          loading="lazy"
          className={`w-full rounded border border-gray-200 bg-white shadow-sm ring-1 ${ringClass}`}
        />
      ) : (
        <div
          aria-label={`페이지 ${page} 없음`}
          className={`flex h-64 w-full items-center justify-center rounded border border-dashed border-gray-300 bg-gray-50 text-xs text-gray-400 ring-1 ${ringClass}`}
        >
          이 문서에는 페이지 {page} 없음
        </div>
      )}
    </figure>
  )
}

function sortedPageNumbers(previews: Record<string, string>): number[] {
  return Object.keys(previews)
    .map((k) => Number.parseInt(k, 10))
    .filter((n) => Number.isFinite(n))
    .sort((a, b) => a - b)
}

/** 긴 미리보기 문자열 말줄임 — 단어 경계 무시 단순 자르기. */
function truncate(text: string, max: number): string {
  const flat = text.replace(/\s+/g, ' ').trim()
  return flat.length > max ? `${flat.slice(0, max)}…` : flat
}

/**
 * 한 행 렌더 — 좌·우 셀을 동시에 출력(2-cell grid item 쌍).
 *
 * change 행은 좌·우 셀을 하나의 `data-op="change"` 래퍼에 묶어, 양쪽 강조 단어가
 * 같은 컨테이너 내 span 들로 함께 잡힌다(테스트가 잠그는 계약).
 */
function DiffRow({
  op,
  imageBlobs,
}: {
  op: DiffOp
  imageBlobs?: Record<string, string>
}) {
  // change 행도 표 행이나 이미지 마커일 수 있다 — 마커/표면 셀 렌더로 위임.
  if (op.op === 'change') {
    const leftRaw = normalizeForWrap(op.left).trim()
    const rightRaw = normalizeForWrap(op.right).trim()
    const isMarker = (s: string) =>
      IMAGE_MARKER_RE.test(s) || TABLE_MARKER_RE.test(s) || TABLE_ROW_RE.test(s)
    if (isMarker(leftRaw) || isMarker(rightRaw)) {
      return (
        <tr data-op="change">
          <td style={leftCellStyleByOp.change} data-side="left">
            <MarkerOrText text={op.left} imageBlobs={imageBlobs} />
          </td>
          <td style={rightCellStyleByOp.change} data-side="right">
            <MarkerOrText text={op.right} imageBlobs={imageBlobs} />
          </td>
        </tr>
      )
    }
    return (
      <tr data-op="change">
        <td style={leftCellStyleByOp.change} data-side="left">
          <WordRun words={op.left_words} fallback={op.left} highlightChanged />
        </td>
        <td style={rightCellStyleByOp.change} data-side="right">
          <WordRun words={op.right_words} fallback={op.right} highlightChanged />
        </td>
      </tr>
    )
  }
  if (op.op === 'add') {
    return (
      <tr data-op="add">
        <td style={leftCellStyleByOp.add} data-side="left" />
        <td style={rightCellStyleByOp.add} data-side="right">
          <MarkerOrText text={op.right} imageBlobs={imageBlobs} />
        </td>
      </tr>
    )
  }
  if (op.op === 'delete') {
    return (
      <tr data-op="delete">
        <td style={leftCellStyleByOp.delete} data-side="left">
          <MarkerOrText text={op.left} imageBlobs={imageBlobs} />
        </td>
        <td style={rightCellStyleByOp.delete} data-side="right" />
      </tr>
    )
  }
  // equal — 좌·우에 같은 텍스트.
  const text = op.left ?? op.right
  return (
    <tr data-op="equal">
      <td style={leftCellStyleByOp.equal} data-side="left">
        <MarkerOrText text={text} imageBlobs={imageBlobs} />
      </td>
      <td style={rightCellStyleByOp.equal} data-side="right">
        <MarkerOrText text={text} imageBlobs={imageBlobs} />
      </td>
    </tr>
  )
}

/**
 * 단어 조각 — `changed`인 단어만 강조(highlightChanged=true). 없으면 평문 폴백.
 *
 * 서버가 `text.split()`로 만든 토큰은 공백을 잃어 그대로 이어 붙이면 단어가 붙어
 * 읽기·줄바꿈 불가가 된다. 토큰 사이에 일반 공백을 보강해 올바른 가독·자동 줄바꿈을
 * 회복한다. NBSP가 섞인 fallback 평문도 같은 이유로 정규화한다.
 */
function WordRun({
  words,
  fallback,
  highlightChanged = false,
}: {
  words?: WordSpan[] | null
  fallback?: string | null
  highlightChanged?: boolean
}) {
  if (!words || words.length === 0) {
    return <>{normalizeForWrap(fallback)}</>
  }
  return (
    <>
      {words.map((word, index) => (
        <span key={index}>
          {index > 0 ? ' ' : ''}
          <span
            style={highlightChanged && word.changed ? changedWordStyle : undefined}
          >
            {word.text}
          </span>
        </span>
      ))}
    </>
  )
}

/**
 * 문서 입력 한 칸 — 드래그앤드랍 + 파일 선택만(텍스트 입력 제거).
 *
 * 선택된 `File` 객체를 `onFile`로 올려 보내고, 그 파일은 `POST /v1/diff/upload`로
 * 멀티파트 전송돼 임시 파일로 추출·즉시 삭제된다(영속 X). 키보드 접근성은 "파일 선택"
 * 버튼(visible)과 보이지 않는 file input(aria-label=`label`)이 함께 지원한다.
 */
function DocDropField({
  label,
  file,
  onFile,
}: {
  label: string
  file: File | null
  onFile: (file: File | null) => void
}) {
  const [dragging, setDragging] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  function take(files: FileList | null) {
    const f = files?.[0]
    if (f) onFile(f)
  }
  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault()
    setDragging(false)
    take(event.dataTransfer.files)
  }
  function handleDragOver(event: DragEvent<HTMLDivElement>) {
    event.preventDefault()
    setDragging(true)
  }
  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    take(event.target.files)
  }

  return (
    <div className="flex flex-1 flex-col gap-3 rounded-xl border border-[#e5e7eb] bg-white p-6 shadow-sm">
      <span className="text-sm font-bold text-[#111827]">{label}</span>
      <div
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragLeave={() => setDragging(false)}
        className={`flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed px-4 py-8 text-center transition-colors ${
          dragging
            ? 'border-[#1d4ed8] bg-[#eef2ff]'
            : 'border-[#e5e7eb] bg-[#f8f9fa]'
        }`}
      >
        <span className="flex h-12 w-12 items-center justify-center rounded-xl bg-[#eef2ff] text-[#1d4ed8]">
          <UploadIcon />
        </span>
        <p className="text-sm text-[#6b7280]">여기로 파일을 끌어다 놓기</p>
        <p className="text-[11px] font-semibold uppercase tracking-widest text-[#9ca3af]">
          또는
        </p>
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          className="cursor-pointer rounded-lg border border-[#d1d5db] bg-white px-4 py-1.5 text-xs font-semibold text-[#374151] hover:bg-[#f3f4f6]"
        >
          파일 선택
        </button>
        <input
          ref={fileInputRef}
          type="file"
          aria-label={label}
          onChange={handleFileChange}
          className="sr-only"
        />
        {file ? (
          <p className="mt-2 max-w-full truncate text-xs font-semibold text-[#1d4ed8]" title={file.name}>
            ✓ {file.name}
          </p>
        ) : null}
      </div>
    </div>
  )
}

/** 업로드 아이콘 — 트레이로 올라가는 화살표(currentColor). */
function UploadIcon({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width="22"
      height="22"
      fill="none"
      aria-hidden
      className={className}
    >
      <path
        d="M9 16h6v-6h4l-7-7-7 7h4v6zm-4 2h14v2H5v-2z"
        fill="currentColor"
      />
    </svg>
  )
}

