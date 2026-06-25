/**
 * 검색 결과 표시 컴포넌트 — 상태(로딩/에러/빈/결과)별 렌더.
 *
 * 결과는 **본문 전체가 아니라 간략 정보 카드**로 보여준다(사용자 요구):
 * 파일 제목 · 1~2줄 요약 · 작성일자 · 원본 소스 · 작성자 · 문서 유형.
 * 모두 백엔드가 주는 hit 메타(`source_url`·`ingested_at`·`author`/`author_department`)
 * 와 본문(`text`)에서 파생한다 — 백엔드 스키마는 그대로 두고 표현만 압축한다.
 *
 * 권한 필터·부서 가중은 **서버가 이미 적용**한 결과를 받는다(프론트는 재구현 금지).
 */

import { useEffect, useState } from 'react'
import type { ApiClient } from '../api/client'
import type { SearchHit } from '../api/types'
import { downloadDoc, PdfPreviewModal } from '../lib/fileActions'
import { addRecentDoc } from '../lib/recentDocs'

export type SearchStatus = 'idle' | 'loading' | 'error' | 'done'

export interface SearchResultsProps {
  status: SearchStatus
  hits: SearchHit[]
  errorMessage: string
  client: ApiClient
  /** 삭제 후 부모가 목록에서 해당 hit를 제거하도록 알린다(마스터 전용 삭제). */
  onDeleted?: (docId: string) => void
}



/** 소스 코드값 → 사람이 읽는 한국어 라벨. */
const SOURCE_LABEL: Record<string, string> = {
  onedrive: 'OneDrive',
  googledrive: 'Google Drive',
  slack: 'Slack',
}

/** `source_url`에서 파일명을 뽑는다 — `local://`·`https://` 스킴·경로를 벗겨낸 basename. */
function fileName(url: string | null | undefined, docId: string): string {
  if (!url) {
    return docId.slice(0, 12)
  }
  const withoutScheme = url.replace(/^[a-z]+:\/\//i, '')
  const base = withoutScheme.split('/').pop()
  return base && base.length > 0 ? base : docId.slice(0, 12)
}

/** 파일명 → 제목(확장자 제거) + 문서 유형(확장자 대문자). 확장자 없으면 유형은 빈 문자열. */
function titleAndType(name: string): { title: string; type: string } {
  const dot = name.lastIndexOf('.')
  if (dot > 0 && dot < name.length - 1) {
    return { title: name.slice(0, dot), type: name.slice(dot + 1).toUpperCase() }
  }
  return { title: name, type: '' }
}

/** 본문을 1~2줄 요약으로 압축 — 공백·개행을 한 칸으로 접고 길면 말줄임한다. */
function brief(text: string): string {
  const flat = text.replace(/\s+/g, ' ').trim()
  const LIMIT = 120
  return flat.length > LIMIT ? `${flat.slice(0, LIMIT)}…` : flat
}

/** ISO 타임스탬프 → `YYYY.MM.DD`. 없거나 파싱 실패면 `—`. */
function formatDate(iso: string | null | undefined): string {
  if (!iso) {
    return '—'
  }
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) {
    return '—'
  }
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  return `${d.getFullYear()}.${mm}.${dd}`
}

/** 원본 링크는 웹 URL(http/https)일 때만 — 로컬 파일(local://)은 클릭 대상이 아니다. */
function webUrl(url: string | null | undefined): string | null {
  return url && /^https?:\/\//i.test(url) ? url : null
}

/**
 * 문서 유형별 파일 아이콘 — design/logo/ 의 브랜드 PNG.
 * 자산은 `frontend/public/file-types/` 에 두어 Vite가 정적 루트로 서빙한다.
 */
function FileIcon({ type }: { type: string }) {
  const ICONS: Record<string, { src: string; alt: string }> = {
    PDF: { src: '/file-types/pdf_icon.png', alt: 'PDF' },
    XLS: { src: '/file-types/excel_icon.png', alt: 'Excel' },
    XLSX: { src: '/file-types/excel_icon.png', alt: 'Excel' },
    DOC: { src: '/file-types/word_icon.png', alt: 'Word' },
    DOCX: { src: '/file-types/word_icon.png', alt: 'Word' },
    PPT: { src: '/file-types/ppt_icon.png', alt: 'PowerPoint' },
    PPTX: { src: '/file-types/ppt_icon.png', alt: 'PowerPoint' },
  }
  const fallback = { src: '/file-types/text_icon.png', alt: type || '파일' }
  const { src, alt } = ICONS[type] ?? fallback
  return <img src={src} alt={alt} className="h-10 w-10 object-contain" />
}

export function SearchResults({ status, hits, errorMessage, client, onDeleted }: SearchResultsProps) {
  // PDF 카드 클릭 시 인라인 뷰어 모달용. 다른 유형은 다운로드만 — 모달 안 띄움.
  const [previewHit, setPreviewHit] = useState<SearchHit | null>(null)
  // 마스터만 삭제 버튼을 본다(서버도 require_master로 강제 — 이건 표시 게이팅).
  const [isMaster, setIsMaster] = useState(false)
  const [deleting, setDeleting] = useState<string | null>(null)
  useEffect(() => {
    let aborted = false
    // Promise.resolve로 감싸 client.me 미정의(테스트 mock 등)도 reject로 흡수.
    Promise.resolve()
      .then(() => client.me())
      .then((m) => { if (!aborted) setIsMaster(!!m.is_master) })
      .catch(() => {})
    return () => { aborted = true }
  }, [client])

  async function handleDelete(hit: SearchHit, label: string) {
    if (!window.confirm(`"${label}" 문서를 삭제할까요? 되돌릴 수 없습니다.`)) return
    setDeleting(hit.doc_id)
    try {
      await client.deleteFile(hit.doc_id)
      onDeleted?.(hit.doc_id)
    } catch {
      window.alert('삭제 실패 — 권한 또는 서버 오류.')
    } finally {
      setDeleting(null)
    }
  }

  if (status === 'idle') {
    return null
  }
  if (status === 'loading') {
    return (
      <p aria-live="polite" className="text-sm text-[#6b7280]">
        검색 중…
      </p>
    )
  }
  if (status === 'error') {
    return (
      <p role="alert" className="text-sm font-medium text-[#dc2626]">
        {errorMessage}
      </p>
    )
  }
  if (hits.length === 0) {
    return (
      <p className="rounded-xl border border-[#e5e7eb] bg-white p-6 text-center text-sm text-[#6b7280]">
        검색 결과가 없습니다.
      </p>
    )
  }
  return (
    <>
    <ul aria-label="검색 결과" className="m-0 flex list-none flex-col gap-4 p-0">
      {hits.map((hit) => {
        const meta = hit.metadata
        // 파일명은 백엔드가 주는 hit.title(원본 파일명)을 우선 쓴다. source_url은
        // 보통 None이라 폴백용일 뿐 — 여기에만 의존하면 doc_id 해시가 제목으로 샌다.
        const name = hit.title || fileName(meta.source_url, hit.doc_id)
        const parsed = titleAndType(name)
        const title = parsed.title
        const type = hit.doc_type ?? parsed.type
        const author = meta.author ?? '—'
        const dept = meta.author_department ?? '—'
        const sourceLabel = SOURCE_LABEL[meta.source] ?? meta.source
        const link = webUrl(meta.source_url)

        return (
          <li
            key={hit.doc_id}
            onClick={() => {
              addRecentDoc({
                id: hit.doc_id,
                title,
                type,
                source: sourceLabel,
                date: formatDate(meta.ingested_at),
              })
              // PDF만 브라우저 내장 뷰어로 인라인 미리보기. 나머지는 원본 다운로드.
              if (type === 'PDF') {
                setPreviewHit(hit)
              } else {
                void downloadDoc(client, hit.doc_id)
              }
            }}
            className="flex cursor-pointer gap-5 rounded-xl border border-[#e5e7eb] bg-white p-6 shadow-sm transition-all hover:-translate-y-0.5 hover:border-[#d1d5db] hover:shadow-md"
          >
            {/* 문서 유형 브랜드 아이콘 */}
            <div className="flex h-10 w-10 shrink-0 items-center justify-center">
              <FileIcon type={type} />
            </div>

            <div className="flex min-w-0 flex-1 flex-col gap-2">
              {/* 제목 + 작성일 */}
              <div className="flex items-start justify-between gap-3">
                <h3 className="m-0 min-w-0 truncate text-[17px] font-semibold text-[#111827]">
                  {title}
                </h3>
                <div className="flex shrink-0 items-center gap-2">
                  <span className="text-xs text-[#9ca3af]">
                    {formatDate(meta.ingested_at)}
                  </span>
                  {isMaster && (
                    <button
                      type="button"
                      aria-label="문서 삭제"
                      disabled={deleting === hit.doc_id}
                      onClick={(e) => {
                        e.stopPropagation()
                        void handleDelete(hit, title)
                      }}
                      className="rounded-md px-2 py-1 text-xs font-medium text-[#dc2626] transition-colors hover:bg-[#fef2f2] disabled:opacity-50"
                    >
                      {deleting === hit.doc_id ? '삭제 중…' : '삭제'}
                    </button>
                  )}
                </div>
              </div>

              {/* 메타: 작성자 ID · 부서 · 원본 소스 · 문서 유형 */}
              <div className="flex flex-wrap items-center gap-2 text-xs text-[#6b7280]">
                <span>
                  <span className="text-[#9ca3af]">작성자</span>{' '}
                  <span className="font-medium text-[#374151]">{author}</span>
                </span>
                <span className="text-[#d1d5db]">•</span>
                <span>
                  <span className="text-[#9ca3af]">부서</span>{' '}
                  <span className="font-medium text-[#374151]">{dept}</span>
                </span>
                <span className="text-[#d1d5db]">•</span>
                <span>{sourceLabel}</span>
                {type ? (
                  <>
                    <span className="text-[#d1d5db]">•</span>
                    <span className="font-semibold text-[#374151]">{type}</span>
                  </>
                ) : null}
              </div>

              {/* 1~2줄 간략 정보 */}
              <p className="line-clamp-2 text-sm leading-relaxed text-[#44474a]">
                {brief(hit.text)}
              </p>

              {link ? (
                <div className="mt-1">
                  <a
                    href={link}
                    target="_blank"
                    rel="noreferrer"
                    onClick={(e) => e.stopPropagation()}
                    className="text-xs font-semibold text-[#1d4ed8] hover:underline"
                  >
                    웹 링크
                  </a>
                </div>
              ) : null}
            </div>
          </li>
        )
      })}
    </ul>
    {previewHit ? (() => {
      const m = previewHit.metadata
      const n = previewHit.title || fileName(m.source_url, previewHit.doc_id)
      const { title } = titleAndType(n)
      return (
        <PdfPreviewModal
          docId={previewHit.doc_id}
          title={title}
          source={SOURCE_LABEL[m.source] ?? m.source}
          date={m.ingested_at ? formatDate(m.ingested_at) : undefined}
          client={client}
          onClose={() => setPreviewHit(null)}
        />
      )
    })() : null}
    </>
  )
}
