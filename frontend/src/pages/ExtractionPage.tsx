/**
 * AI OCR 페이지 — 사이드바 독립 메뉴(문서 파싱과 분리).
 *
 * 이미지/PDF 업로드 → 좌측 1:1 원본 미리보기 + 우측 템플릿(스키마) 작성·추출 패널.
 * 추출/스키마 권한은 서버가 권위로 강제(마스터 게이팅) — 여기선 UI만.
 */

import { useEffect, useRef, useState } from 'react'
import type { ApiClient } from '../api/client'
import type { ExtractResponse } from '../api/types'
import { Dropzone } from '../components/Dropzone'
import { ImageViewer } from '../components/ImageViewer'
import { ZoomControls } from '../components/ZoomControls'
import { ExtractionPanel } from './ExtractionPanel'

// 지원 포맷(PDF + 스캔 이미지) — ExtractionPanel canExtract 와 일치.
const ACCEPT = '.pdf,.png,.jpg,.jpeg,.webp,.bmp,.tif,.tiff,.gif'

export function ExtractionPage({ client }: { client: ApiClient }) {
  const [file, setFile] = useState<File | null>(null)
  // 추출 결과 + hover 근거 페이지 — 좌측 원본 미리보기를 근거(B-Box) 프리뷰로 전환·강조한다.
  const [result, setResult] = useState<ExtractResponse | null>(null)
  const [hoverPage, setHoverPage] = useState<number | null>(null)
  const docType = file ? (file.name.split('.').pop() ?? '').toUpperCase() : ''
  const hasEvidence =
    result != null && Object.keys(result.evidence_previews ?? {}).length > 0

  function onFiles(files: FileList | File[]) {
    setFile(files[0] ?? null)
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col p-6">
      <div className="mb-4 flex items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-bold text-[#111827]">AI OCR</h1>
          <p className="text-xs text-[#6b7280]">
            이미지·PDF를 올리면 원본 미리보기 옆에서 추출 템플릿을 작성하고 값을 뽑습니다.
          </p>
        </div>
        {file && (
          <div className="flex shrink-0 items-center gap-2">
            <span className="max-w-[240px] truncate rounded-md bg-[#eef2ff] px-2 py-1 text-xs font-semibold text-[#1d4ed8]">
              {file.name}
            </span>
            <Dropzone
              onFiles={onFiles}
              accept={ACCEPT}
              ariaLabel="다른 파일"
              className="rounded-md border border-[#d1d5db] px-3 py-1.5 text-xs font-semibold text-[#374151] hover:bg-[#f3f4f6]"
              label="다른 파일"
            />
            <button
              type="button"
              onClick={() => setFile(null)}
              className="rounded-md border border-[#d1d5db] px-3 py-1.5 text-xs font-semibold text-[#374151] hover:bg-[#f3f4f6]"
            >
              초기화면으로
            </button>
          </div>
        )}
      </div>

      {!file ? (
        <Dropzone
          onFiles={onFiles}
          accept={ACCEPT}
          ariaLabel="이미지·PDF 업로드"
          className="flex flex-1 flex-col items-center justify-center rounded-xl border-2 border-dashed border-[#d1d5db] bg-[#f8fafc] p-10 text-center hover:border-[#1d4ed8] hover:bg-[#eef2ff]"
          label={
            <>
              <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#9ca3af" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                <polyline points="14 2 14 8 20 8" />
                <line x1="12" y1="18" x2="12" y2="12" />
                <polyline points="9 15 12 12 15 15" />
              </svg>
              <span className="mt-3 text-sm font-semibold text-[#374151]">이미지·PDF를 업로드하세요</span>
              <span className="mt-1 text-xs text-[#6b7280]">PDF · 스캔 이미지(PNG/JPG 등)</span>
            </>
          }
        />
      ) : (
        // 좌:원본 미리보기(1/3)  우:템플릿 작성·추출(2/3).
        <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 lg:grid-cols-3">
          <section
            aria-label="원본 미리보기"
            className="flex min-h-0 flex-col overflow-hidden rounded-lg border border-[#e5e7eb] bg-white lg:col-span-1"
          >
            <header className="border-b border-[#e5e7eb] bg-[#f8fafc] px-4 py-2">
              <span className="text-[11px] font-bold uppercase tracking-wide text-[#1f2937]">
                {hasEvidence ? '원본 미리보기 · 근거 위치' : '원본 미리보기'}
              </span>
            </header>
            <div className="min-h-0 flex-1 overflow-auto bg-[#f8f9fa]">
              {hasEvidence ? (
                <EvidencePreview previews={result!.evidence_previews} hoverPage={hoverPage} />
              ) : (
                <DocPreview file={file} />
              )}
            </div>
          </section>

          <div className="flex min-h-0 flex-col lg:col-span-2">
            <ExtractionPanel
              client={client}
              file={file}
              docType={docType}
              onReset={() => setFile(null)}
              onResult={setResult}
              onHoverEvidence={setHoverPage}
            />
          </div>
        </div>
      )}
    </div>
  )
}

/**
 * 근거(B-Box) 프리뷰 — 추출 결과의 페이지별 PNG(서버가 근거 사각형을 그려 보냄)를
 * 좌측 원본 미리보기 칸에 표시한다. 우측 결과표에서 필드에 hover하면(hoverPage) 해당
 * 페이지가 강조되고 그 위치로 스크롤된다. 확대/축소(ZoomControls) 지원.
 */
function EvidencePreview({
  previews,
  hoverPage,
}: {
  previews: Record<string, string>
  hoverPage: number | null
}) {
  const pages = Object.keys(previews)
    .map((k) => Number.parseInt(k, 10))
    .filter((n) => Number.isFinite(n))
    .sort((a, b) => a - b)
  const [zoom, setZoom] = useState(1)
  const refs = useRef<Map<number, HTMLDivElement | null>>(new Map())

  useEffect(() => {
    if (hoverPage != null) {
      refs.current.get(hoverPage)?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    }
  }, [hoverPage])

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between px-3 py-2">
        <span className="text-[10px] font-bold uppercase tracking-wide text-[#6b7280]">
          근거 위치 · 필드 hover로 강조
        </span>
        <ZoomControls zoom={zoom} onZoom={setZoom} />
      </div>
      <div className="flex-1 overflow-auto px-3 pb-3">
        <div className="flex flex-col gap-3">
          {pages.map((page) => (
            <div
              key={page}
              ref={(el) => {
                refs.current.set(page, el)
              }}
              className={`overflow-auto rounded-lg border bg-white p-1.5 transition-all ${
                hoverPage === page
                  ? 'border-[#f59e0b] shadow-md ring-2 ring-[#fcd34d]'
                  : 'border-[#e5e7eb]'
              }`}
            >
              <span className="mb-1 inline-block rounded bg-[#f3f4f6] px-1.5 py-0.5 text-[10px] font-bold text-[#4b5563]">
                PAGE {page}
              </span>
              <img
                src={previews[String(page)]}
                alt={`근거 페이지 ${page}`}
                loading="lazy"
                style={{ width: `${zoom * 100}%` }}
                className="max-w-none rounded border border-[#e5e7eb]"
              />
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

/**
 * 원본 미리보기 — 이미지는 `<img>`(1:1 비율 유지), PDF는 브라우저 내장 뷰어(`<iframe>`).
 * objectURL은 파일 변경/언마운트 시 해제한다(메모리 누수 방지).
 */
function DocPreview({ file }: { file: File }) {
  const [url, setUrl] = useState<string | null>(null)

  useEffect(() => {
    const u = URL.createObjectURL(file)
    setUrl(u)
    return () => URL.revokeObjectURL(u)
  }, [file])

  if (!url) return null
  const isPdf =
    file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf')

  return isPdf ? (
    <iframe src={url} title="문서 미리보기" className="h-full w-full border-0" />
  ) : (
    <ImageViewer src={url} alt="문서 미리보기" />
  )
}
