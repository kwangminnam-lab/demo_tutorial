/**
 * 파일 원본 접근 액션 — 다운로드 + PDF 인라인 미리보기 공통 모듈.
 *
 * 검색 결과·최근 본 문서 등 여러 진입점이 같은 동작을 하므로 한 곳으로 모은다.
 * 권한·존재 확인은 백엔드 `/v1/files/{doc_id}` 가 한다(401/403/404 그대로 전파).
 * 토큰은 fetch 헤더로만 전송하며 URL·로그에 싣지 않는다(누설 방지).
 */

import { useEffect, useState } from 'react'
import type { ApiClient } from '../api/client'
import { ApiError } from '../api/client'

/** 원본 접근 실패를 사용자 메시지로 변환(상태별). */
export function fileErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 404) return '파일을 찾을 수 없습니다.'
    if (error.status === 403) return '접근 권한이 없습니다.'
    return '파일을 열 수 없습니다. 잠시 후 다시 시도해 주세요.'
  }
  return '파일을 열 수 없습니다. 잠시 후 다시 시도해 주세요.'
}

/**
 * 원본 파일명으로 다운로드한다.
 *
 * blob URL을 새 탭으로 여는 방식은 원본 Content-Disposition을 잃어 blob 해시가
 * 파일명이 된다. 대신 `<a download="<원본명>">`를 프로그래매틱 클릭해 백엔드가
 * 보낸 한글 파일명(`filename*=UTF-8''…`)으로 저장한다.
 */
export async function downloadDoc(
  client: ApiClient,
  docId: string,
): Promise<void> {
  try {
    const { blob, filename } = await client.fetchFile(docId, true)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    a.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 1_000)
  } catch (error) {
    window.alert(fileErrorMessage(error))
  }
}

/**
 * PDF 인라인 미리보기 모달 — 브라우저 내장 PDF 뷰어(`<iframe>`) 사용.
 * 비-PDF는 호출자가 다운로드로 우회한다(이 모달은 PDF만 처리).
 */
export function PdfPreviewModal({
  docId,
  title,
  source,
  date,
  client,
  onClose,
}: {
  docId: string
  title: string
  source?: string
  date?: string
  client: ApiClient
  onClose: () => void
}) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let aborted = false
    let url: string | null = null
    void (async () => {
      try {
        const { blob } = await client.fetchFile(docId, false)
        if (aborted) return
        url = URL.createObjectURL(blob)
        setBlobUrl(url)
      } catch (e) {
        if (!aborted) setError(fileErrorMessage(e))
      } finally {
        if (!aborted) setLoading(false)
      }
    })()
    return () => {
      aborted = true
      if (url) URL.revokeObjectURL(url)
    }
  }, [docId, client])

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div
      role="dialog"
      aria-label="PDF 미리보기"
      onClick={onClose}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-6"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="flex max-h-[92vh] w-full max-w-5xl flex-col overflow-hidden rounded-xl bg-white shadow-xl"
      >
        <header className="flex items-start justify-between gap-4 border-b border-[#e5e7eb] px-6 py-4">
          <div className="min-w-0">
            <h3 className="truncate text-base font-semibold text-[#111827]">
              {title}
              <span className="ml-2 text-xs font-bold text-[#6b7280]">PDF</span>
            </h3>
            {source || date ? (
              <p className="mt-1 text-xs text-[#6b7280]">
                {source ?? ''}
                {source && date ? ' · ' : ''}
                {date ?? ''}
              </p>
            ) : null}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="닫기"
            className="cursor-pointer rounded-lg p-1.5 text-[#6b7280] hover:bg-[#f3f4f6]"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="6" y1="6" x2="18" y2="18" />
              <line x1="6" y1="18" x2="18" y2="6" />
            </svg>
          </button>
        </header>
        <div className="flex-1 overflow-auto bg-[#f8f9fa]">
          {error ? (
            <p role="alert" className="p-6 text-sm font-medium text-[#dc2626]">
              {error}
            </p>
          ) : loading || !blobUrl ? (
            <p aria-live="polite" className="p-6 text-sm text-[#6b7280]">
              PDF 불러오는 중…
            </p>
          ) : (
            <iframe src={blobUrl} title={title} className="h-[80vh] w-full border-0" />
          )}
        </div>
      </div>
    </div>
  )
}
