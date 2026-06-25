/**
 * 문서 파싱 페이지 — 단일 파일 드래그앤드랍 → HTML/JSON 미리보기 + 다운로드.
 *
 * 진행 중 fetch는 parseStore에 보유 — 탭 이동에도 끊기지 않음 (diffStore와 같은 패턴).
 */

import { useMemo, useRef, useState, useSyncExternalStore, type FormEvent } from 'react'
import { ApiError, type ApiClient } from '../api/client'
import { Dropzone } from '../components/Dropzone'
import { ZoomControls } from '../components/ZoomControls'
import { CircularProgress, useTimedProgress } from '../app/CircularProgress'
import { parseStore } from '../lib/parseStore'

const ACCEPTED_EXTS =
  '.pdf,.docx,.pptx,.xlsx,.xlsm,.html,.htm,.txt,.md,.png,.jpg,.jpeg,.webp,.bmp,.tif,.tiff,.gif'

export function ParsePage({ client }: { client: ApiClient }) {
  const storeState = useSyncExternalStore(parseStore.subscribe, parseStore.getSnapshot)
  const { file, status, result, errorMessage } = storeState
  const [tab, setTab] = useState<'preview' | 'html' | 'json'>('html')
  const [downloadError, setDownloadError] = useState('')

  function handleFile(f: File | null) {
    parseStore.setFile(f)
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!file || status === 'loading') return
    void parseStore.parse(client, file)
  }

  function handleReset() {
    parseStore.reset()
  }

  function downloadHtml() {
    if (!result) return
    const blob = new Blob([result.html], { type: 'text/html;charset=utf-8' })
    triggerDownload(blob, baseName(result.filename) + '.html')
  }

  function downloadJson() {
    if (!result) return
    const blob = new Blob([JSON.stringify(result.json_data, null, 2)], {
      type: 'application/json;charset=utf-8',
    })
    triggerDownload(blob, baseName(result.filename) + '.json')
  }

  async function downloadJsonl() {
    if (!result || !file) return
    setDownloadError('')
    try {
      const text = await client.parseJsonl(file)
      const blob = new Blob([text], { type: 'application/x-ndjson;charset=utf-8' })
      triggerDownload(blob, baseName(result.filename) + '.jsonl')
    } catch (err) {
      setDownloadError(err instanceof ApiError ? err.message : 'JSONL 다운로드에 실패했습니다.')
    }
  }

  return (
    <section className="flex h-full flex-col bg-white p-6">
      <header className="mb-4">
        <h1 className="text-xl font-bold text-[#111827]">문서 파싱</h1>
        <p className="mt-1 text-sm text-[#6b7280]">
          파일을 드래그앤드랍하거나 선택해 추출 결과를 HTML/JSON으로 미리보고 다운로드합니다.
          (적재되지 않음 — 임시 파싱만)
        </p>
      </header>

      {status === 'loading' ? (
        <ParseLoadingPanel file={file} onCancel={handleReset} />
      ) : status !== 'done' || result === null ? (
        <form
          onSubmit={handleSubmit}
          className="mx-auto flex w-full max-w-2xl flex-col gap-4"
        >
          <Dropzone
            onFiles={(files) => handleFile(files[0] ?? null)}
            accept={ACCEPTED_EXTS}
            ariaLabel="파일 선택"
            className="rounded-xl border-2 border-dashed border-[#d1d5db] bg-white p-12 text-center hover:border-[#9ca3af]"
            label={
              file ? (
                <div>
                  <p className="text-sm font-semibold text-[#111827]">{file.name}</p>
                  <p className="mt-1 text-xs text-[#6b7280]">
                    {Math.round(file.size / 1024)} KB
                  </p>
                  <p className="mt-3 text-xs text-[#9ca3af]">
                    다른 파일로 교체하려면 클릭/드랍
                  </p>
                </div>
              ) : (
                <div>
                  <p className="text-sm font-semibold text-[#374151]">
                    파일을 여기에 드랍하거나 클릭해서 선택
                  </p>
                  <p className="mt-2 text-xs text-[#9ca3af]">
                    지원: PDF · DOCX · PPTX · XLSX · XLSM · HTML · TXT · MD · 스캔
                    이미지(PNG/JPG)
                  </p>
                </div>
              )
            }
          />
          {errorMessage && (
            <div
              role="alert"
              className="rounded-lg border border-[#fca5a5] bg-[#fef2f2] px-4 py-3 text-sm text-[#b91c1c]"
            >
              {errorMessage}
            </div>
          )}
          <div className="flex justify-end gap-2">
            {file && (
              <button
                type="button"
                onClick={handleReset}
                className="rounded-lg border border-[#d1d5db] px-4 py-2 text-sm font-semibold text-[#374151] hover:bg-[#f3f4f6]"
              >
                초기화
              </button>
            )}
            <button
              type="submit"
              disabled={!file}
              className="rounded-lg bg-[#1d4ed8] px-4 py-2 text-sm font-semibold text-white hover:bg-[#1e40af] disabled:cursor-not-allowed disabled:opacity-40"
            >
              파싱 시작
            </button>
          </div>
        </form>
      ) : (
        <ResultView
          result={result}
          tab={tab}
          onTab={setTab}
          onBack={handleReset}
          onDownloadHtml={downloadHtml}
          onDownloadJson={downloadJson}
          onDownloadJsonl={downloadJsonl}
          downloadError={downloadError}
        />
      )}
    </section>
  )
}

/**
 * 파싱 로딩 패널 — 원형 progress + 예상 소요 + 단계 캡션.
 *
 * Docling 첫 호출은 모델 로드 포함 ~30초, 이후 페이지당 ~10초. 파일 크기로 페이지
 * 개수를 추정해 예상 시간 보정 (대략 1MB당 4페이지).
 */
function ParseLoadingPanel({
  file,
  onCancel,
}: {
  file: File | null
  onCancel: () => void
}) {
  const fileSizeMB = file ? file.size / (1024 * 1024) : 1
  const estimatedPages = Math.max(1, Math.min(50, Math.round(fileSizeMB * 4)))
  // 첫 호출이라 모델 로드 포함 ~25초 + 페이지당 10초.
  const estimatedMs = 25_000 + estimatedPages * 10_000
  const progress = useTimedProgress(true, estimatedMs)
  const remainingSec = Math.max(
    0,
    Math.round((estimatedMs * (100 - progress)) / 100 / 1000),
  )
  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col items-center justify-center gap-6 py-12">
      <div className="relative">
        <CircularProgress
          value={progress}
          size={140}
          thickness={10}
          label={`${Math.floor(progress)}%`}
          caption="Docling"
          ariaLabel="파싱 진행률"
        />
      </div>
      <div className="text-center">
        <p className="text-sm font-semibold text-[#111827]">
          {file?.name ?? '파일'} 파싱 중
        </p>
        <p className="mt-1 text-xs text-[#6b7280]">
          예상 약 {Math.round(estimatedMs / 1000)}초 · 남은 시간 약 {remainingSec}초
        </p>
        <p className="mt-1 text-[11px] text-[#9ca3af]">
          (첫 호출은 Docling 레이아웃 모델 로드 포함 — 다음부터 빠름)
        </p>
      </div>
      <button
        type="button"
        onClick={onCancel}
        className="rounded-lg border border-[#d1d5db] px-4 py-2 text-xs font-semibold text-[#374151] hover:bg-[#f3f4f6]"
      >
        취소
      </button>
    </div>
  )
}

/** 페이지 대비 정규화 bbox — [x, y, w, h] ∈ [0,1], top-left 기준. */
type BBox = [number, number, number, number]

/** json_data.sections 한 항목 — 문단/표 블록 + 페이지 + 정규화 bbox(가용 시). */
interface ParseBlock {
  index?: number
  kind?: string
  page: number
  bbox: BBox | null
  text?: string
}

function ResultView({
  result,
  tab,
  onTab,
  onBack,
  onDownloadHtml,
  onDownloadJson,
  onDownloadJsonl,
  downloadError,
}: {
  result: {
    filename: string
    doc_type: string
    html: string
    json_data: Record<string, unknown>
    page_previews?: Record<string, string>
  }
  tab: 'preview' | 'html' | 'json'
  onTab: (t: 'preview' | 'html' | 'json') => void
  onBack: () => void
  onDownloadHtml: () => void
  onDownloadJson: () => void
  onDownloadJsonl: () => void
  downloadError: string
}) {
  const previews = result.page_previews ?? {}
  const pageNumbers = Object.keys(previews)
    .map((k) => Number.parseInt(k, 10))
    .filter((n) => Number.isFinite(n))
    .sort((a, b) => a - b)
  const hasPreviews = pageNumbers.length > 0

  const [hoveredPage, setHoveredPage] = useState<number | null>(null)
  // 코드 라인 hover 시 강조할 **실제 문단/표 위치**(페이지 + 정규화 bbox). 없으면 페이지 단위.
  const [hoveredBox, setHoveredBox] = useState<{ page: number; bbox: BBox } | null>(null)
  const [zoom, setZoom] = useState(1)
  const pageRefs = useRef<Map<number, HTMLDivElement | null>>(new Map())

  const codeText =
    tab === 'html' ? result.html : JSON.stringify(result.json_data, null, 2)
  const codeLines = codeText.split('\n')

  // json_data.sections — 문단/표 단위 블록(가용 시 페이지 내 정규화 bbox 포함).
  // (구조뷰 prop `sections`와 구분 위해 blockSections 로 둔다.)
  const blockSections = useMemo<ParseBlock[]>(() => {
    const raw = (result.json_data as { sections?: unknown }).sections
    return Array.isArray(raw) ? (raw as ParseBlock[]) : []
  }, [result.json_data])

  // JSON 코드 라인 → 블록 매핑: 각 블록 객체는 `"index": N`으로 시작하므로 그 마커를
  // 세어 라인을 블록에 귀속시킨다(JSON.stringify 포맷에 견고). HTML 탭은 매핑 없음.
  const lineSection = useMemo<(ParseBlock | null)[]>(() => {
    const map: (ParseBlock | null)[] = new Array(codeLines.length).fill(null)
    if (tab !== 'json' || blockSections.length === 0) return map
    let pos = -1
    for (let i = 0; i < codeLines.length; i++) {
      if (/"index":\s*\d+/.test(codeLines[i])) pos += 1
      if (pos >= 0 && pos < blockSections.length) map[i] = blockSections[pos]
    }
    return map
  }, [codeLines, blockSections, tab])

  // 라인 → 페이지 폴백 매핑: bbox 없는 경우(HTML 탭/좌표 미가용) 균등 분할 근사.
  const linesPerPage = hasPreviews
    ? Math.max(1, Math.ceil(codeLines.length / pageNumbers.length))
    : 0
  function lineToPage(lineIdx: number): number | null {
    if (!hasPreviews) return null
    const idx = Math.min(pageNumbers.length - 1, Math.floor(lineIdx / linesPerPage))
    return pageNumbers[idx] ?? null
  }

  // 코드 라인 hover → 매치되는 문단/표의 실제 위치(bbox)를 강조. bbox 없으면 페이지 단위.
  function handleLineHover(lineIdx: number) {
    const section = lineSection[lineIdx]
    if (section && section.page != null) {
      setHoveredPage(section.page)
      setHoveredBox(
        Array.isArray(section.bbox) ? { page: section.page, bbox: section.bbox } : null,
      )
      scrollToPage(section.page)
      return
    }
    const page = lineToPage(lineIdx)
    if (page === null) return
    setHoveredBox(null)
    if (page !== hoveredPage) {
      setHoveredPage(page)
      scrollToPage(page)
    }
  }

  function scrollToPage(page: number) {
    const target = pageRefs.current.get(page)
    if (target) target.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }

  return (
    // 바깥 세로 스크롤 + grid 최소 높이 — 프리뷰·코드 패널을 충분히 키우고 넘치면 스크롤.
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
      <div className="mb-3 flex items-center justify-between">
        <div>
          <span className="text-sm font-semibold text-[#111827]">{result.filename}</span>
          {result.doc_type && (
            <span className="ml-2 rounded-md bg-[#eef2ff] px-2 py-0.5 text-xs font-bold text-[#1d4ed8]">
              {result.doc_type}
            </span>
          )}
          {hasPreviews && (
            <span className="ml-2 rounded-md bg-[#fef3c7] px-2 py-0.5 text-xs font-bold text-[#92400e]">
              {pageNumbers.length}p
            </span>
          )}
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={onDownloadHtml}
            className="rounded-md border border-[#d1d5db] px-3 py-1.5 text-xs font-semibold text-[#374151] hover:bg-[#f3f4f6]"
          >
            HTML 다운로드
          </button>
          <button
            type="button"
            onClick={onDownloadJson}
            className="rounded-md border border-[#d1d5db] px-3 py-1.5 text-xs font-semibold text-[#374151] hover:bg-[#f3f4f6]"
          >
            JSON 다운로드
          </button>
          <button
            type="button"
            onClick={onDownloadJsonl}
            className="rounded-md border border-[#d1d5db] px-3 py-1.5 text-xs font-semibold text-[#374151] hover:bg-[#f3f4f6]"
          >
            JSONL 다운로드
          </button>
          <button
            type="button"
            onClick={onBack}
            className="rounded-md border border-[#d1d5db] px-3 py-1.5 text-xs font-semibold text-[#374151] hover:bg-[#f3f4f6]"
          >
            새 파일
          </button>
        </div>
      </div>

      {downloadError && (
        <div
          role="alert"
          className="mb-2 rounded-lg border border-[#fca5a5] bg-[#fef2f2] px-3 py-2 text-xs text-[#b91c1c]"
        >
          {downloadError}
        </div>
      )}

      <div className="grid min-h-[600px] flex-1 grid-cols-1 gap-3 lg:grid-cols-2">
        {/* 좌: 페이지 프리뷰 (이미지 기반) — 미지원 시 본문 안내 */}
        <section
          aria-label="페이지 프리뷰"
          className="flex min-h-0 flex-col overflow-hidden rounded-lg border border-[#e5e7eb] bg-white"
        >
          <header className="flex items-center justify-between border-b border-[#e5e7eb] bg-[#f8fafc] px-4 py-2">
            <span className="text-[11px] font-bold uppercase tracking-wide text-[#1f2937]">
              원본 페이지 프리뷰
            </span>
            <div className="flex items-center gap-2">
              {hasPreviews && <ZoomControls zoom={zoom} onZoom={setZoom} />}
              {hoveredPage !== null && (
                <span className="rounded bg-[#fef3c7] px-2 py-0.5 text-[10px] font-bold text-[#92400e]">
                  PAGE {hoveredPage}
                </span>
              )}
            </div>
          </header>
          {hasPreviews ? (
            <div className="flex-1 overflow-y-auto p-3">
              <div className="flex flex-col gap-4">
                {pageNumbers.map((page) => (
                  <ParsePagePreview
                    key={page}
                    page={page}
                    src={previews[String(page)]}
                    zoom={zoom}
                    highlighted={hoveredPage === page}
                    box={hoveredBox && hoveredBox.page === page ? hoveredBox.bbox : null}
                    refCallback={(el) => pageRefs.current.set(page, el)}
                  />
                ))}
              </div>
            </div>
          ) : (
            <div className="flex flex-1 items-center justify-center p-6 text-center text-xs text-[#6b7280]">
              이 포맷은 페이지 프리뷰 미지원
              <br />
              (LibreOffice/pymupdf 부재 또는 페이지 개념 없음)
            </div>
          )}
        </section>

        {/* 우: HTML / JSON 코드 탭 전환. 프리뷰:우 = 1:1. (필드 추출은 /extract 메뉴로 분리) */}
        <section
          aria-label="파싱 코드"
          className="flex min-h-0 flex-col overflow-hidden rounded-lg border border-[#e5e7eb] bg-white"
        >
          <header className="flex items-center justify-between border-b border-[#e5e7eb] bg-[#f8fafc] px-1 py-1">
            <div role="tablist" aria-label="코드 형식" className="flex gap-0">
              <button
                role="tab"
                aria-selected={tab === 'preview'}
                type="button"
                onClick={() => onTab('preview')}
                className={`px-4 py-1.5 text-xs font-semibold transition-colors ${
                  tab === 'preview'
                    ? 'rounded bg-white text-[#1d4ed8] shadow-sm'
                    : 'text-[#6b7280] hover:text-[#374151]'
                }`}
              >
                프리뷰
              </button>
              <button
                role="tab"
                aria-selected={tab === 'html'}
                type="button"
                onClick={() => onTab('html')}
                className={`px-4 py-1.5 text-xs font-semibold transition-colors ${
                  tab === 'html'
                    ? 'rounded bg-white text-[#1d4ed8] shadow-sm'
                    : 'text-[#6b7280] hover:text-[#374151]'
                }`}
              >
                HTML 코드
              </button>
              <button
                role="tab"
                aria-selected={tab === 'json'}
                type="button"
                onClick={() => onTab('json')}
                className={`px-4 py-1.5 text-xs font-semibold transition-colors ${
                  tab === 'json'
                    ? 'rounded bg-white text-[#1d4ed8] shadow-sm'
                    : 'text-[#6b7280] hover:text-[#374151]'
                }`}
              >
                JSON 코드
              </button>
            </div>
            <span className="px-3 text-[10px] text-[#6b7280]">
              {tab === 'preview'
                ? '요소 타입별 구분 미리보기'
                : hasPreviews
                  ? '라인 hover → 문단/표 위치 강조'
                  : '코드 미리보기'}
            </span>
          </header>
          {tab === 'preview' ? (
            <PreviewView html={result.html} />
          ) : (
            <CodeView
              lines={codeLines}
              onHoverLine={handleLineHover}
              onLeaveCode={() => {
                setHoveredPage(null)
                setHoveredBox(null)
              }}
              language={tab === 'json' ? 'json' : 'html'}
            />
          )}
        </section>
      </div>
    </div>
  )
}

/**
 * 페이지 프리뷰 카드 — hover된 페이지는 ring + 그림자 강조.
 */
function ParsePagePreview({
  page,
  src,
  zoom,
  highlighted,
  box,
  refCallback,
}: {
  page: number
  src: string | undefined
  zoom: number
  highlighted: boolean
  /** 이 페이지에서 강조할 문단/표 위치(정규화 bbox). null이면 페이지 단위 강조만. */
  box: BBox | null
  refCallback: (el: HTMLDivElement | null) => void
}) {
  return (
    <div
      ref={refCallback}
      className={`scroll-m-3 overflow-auto rounded-lg border bg-white p-2 transition-all ${
        highlighted
          ? 'border-[#f59e0b] shadow-lg ring-2 ring-[#fcd34d]'
          : 'border-[#e5e7eb]'
      }`}
    >
      <div className="mb-1 flex items-center gap-2">
        <span
          className={`rounded px-2 py-0.5 text-[10px] font-bold tracking-wider ${
            highlighted ? 'bg-[#fef3c7] text-[#92400e]' : 'bg-[#f3f4f6] text-[#4b5563]'
          }`}
        >
          PAGE {page}
        </span>
      </div>
      {src ? (
        // 이미지+오버레이를 한 래퍼로 묶어 bbox %가 이미지에 정확히 정렬되게 한다.
        <div className="relative" style={{ width: `${zoom * 100}%` }}>
          <img
            src={src}
            alt={`페이지 ${page}`}
            loading="lazy"
            className="block w-full max-w-none rounded border border-[#e5e7eb] bg-white"
          />
          {box && (
            <div
              aria-hidden="true"
              className="pointer-events-none absolute rounded-sm border-2 border-[#f59e0b] bg-[#fcd34d]/25 transition-all"
              style={{
                left: `${box[0] * 100}%`,
                top: `${box[1] * 100}%`,
                width: `${box[2] * 100}%`,
                height: `${box[3] * 100}%`,
              }}
            />
          )}
        </div>
      ) : (
        <div className="flex h-32 items-center justify-center rounded border border-dashed border-[#d1d5db] bg-[#f9fafb] text-[11px] text-[#9ca3af]">
          페이지 {page} 없음
        </div>
      )}
    </div>
  )
}

/**
 * 코드 뷰어 — 라인 번호 + 각 라인 hover 추적. monospace 모노 톤.
 */
function CodeView({
  lines,
  onHoverLine,
  onLeaveCode,
  language,
}: {
  lines: string[]
  onHoverLine: (lineIdx: number) => void
  onLeaveCode: () => void
  language: 'html' | 'json'
}) {
  const [hoveredLine, setHoveredLine] = useState<number | null>(null)
  return (
    <div
      aria-label={`${language} 코드`}
      onMouseLeave={() => {
        setHoveredLine(null)
        onLeaveCode()
      }}
      className="flex-1 overflow-auto bg-[#0f172a] font-mono text-[12px] leading-5 text-[#e2e8f0]"
    >
      <table className="w-full border-separate border-spacing-0">
        <tbody>
          {lines.map((line, idx) => (
            <tr
              key={idx}
              onMouseEnter={() => {
                setHoveredLine(idx)
                onHoverLine(idx)
              }}
              className={
                hoveredLine === idx
                  ? 'bg-[#1e293b]'
                  : 'hover:bg-[#1e293b]'
              }
            >
              <td className="select-none border-r border-[#1e293b] px-2 py-0 text-right text-[10px] text-[#64748b]">
                {idx + 1}
              </td>
              <td className="whitespace-pre-wrap break-all px-3 py-0 text-[#e2e8f0]">
                {line || ' '}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/**
 * 프리뷰 — 파싱 결과 HTML(result.html)을 그대로 렌더한다(코드 아닌 사람용 미리보기).
 *
 * sections(평문 블록)로 재구성하지 않고 **실제 HTML**을 sandbox iframe에 띄운다 — 표·이미지·
 * 제목 계층이 문서 순서(위→아래) 그대로 보존된다. sandbox(빈 값)로 스크립트 실행은 차단하되
 * data: URL 이미지는 렌더된다(백엔드가 이미지를 data URL <img>로 인라인함).
 */
function PreviewView({ html }: { html: string }) {
  if (!html.trim()) {
    return (
      <div className="flex flex-1 items-center justify-center text-center text-xs text-[#9ca3af]">
        미리볼 HTML이 없습니다
      </div>
    )
  }
  return (
    <iframe
      title="문서 프리뷰"
      srcDoc={html}
      sandbox=""
      className="min-h-0 w-full flex-1 border-0 bg-white"
    />
  )
}

function baseName(filename: string): string {
  const dot = filename.lastIndexOf('.')
  return dot > 0 ? filename.slice(0, dot) : filename
}

function triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
