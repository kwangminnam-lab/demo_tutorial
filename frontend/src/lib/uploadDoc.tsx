/**
 * 문서 추가 모달 — 어디서든(사이드바 버튼) 호출 가능한 글로벌 업로드 다이얼로그.
 *
 * 업로드 성공 시 `window.dispatchEvent(new Event(UPLOAD_DONE_EVENT))`를 발사해
 * 대시보드 등 facets 표시 화면이 자체적으로 갱신할 수 있게 한다(Context 의존 없이
 * 페이지 간 결합도 낮음). 토큰은 `ApiClient` 내부에서만 헤더로 전송한다.
 */

import { useEffect, useState, type FormEvent } from 'react'
import type { ApiClient } from '../api/client'
import { ApiError } from '../api/client'
import type { SourceType } from './../api/types'
import { Dropzone } from '../components/Dropzone'
import { CircularProgress, useTimedProgress } from '../app/CircularProgress'

/** 업로드 성공 시 발사되는 글로벌 이벤트명 — 대시보드 등이 listen. */
export const UPLOAD_DONE_EVENT = 'docux:uploaded'

export function UploadModal({
  client,
  onClose,
}: {
  client: ApiClient
  onClose: () => void
}) {
  const [source, setSource] = useState<SourceType>('onedrive')
  const [file, setFile] = useState<File | null>(null)
  const [status, setStatus] = useState<'idle' | 'uploading' | 'done' | 'error'>('idle')
  const [message, setMessage] = useState('')

  // Esc 닫기.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape' && status !== 'uploading') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, status])

  function handleFiles(files: FileList | File[]) {
    const picked = files[0] ?? null
    setFile(picked)
    setStatus('idle')
    setMessage('')
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!file) return
    setStatus('uploading')
    setMessage('')
    try {
      const report = await client.ingestUpload(file, source)
      if (report.ok) {
        setStatus('done')
        setMessage(`적재 완료: ${file.name} → 청크 ${report.chunk_count ?? '?'}`)
        window.dispatchEvent(new Event(UPLOAD_DONE_EVENT))
      } else {
        setStatus('error')
        setMessage(report.error ?? '적재 실패')
      }
    } catch (error) {
      setStatus('error')
      if (error instanceof ApiError) {
        if (error.status === 413) setMessage('파일이 너무 큽니다(상한 80 MiB).')
        else if (error.status === 415) setMessage('지원하지 않는 형식입니다(pdf/docx/pptx/xlsx/txt).')
        else if (error.status === 401) setMessage('인증이 필요합니다.')
        else setMessage('업로드 실패 — 잠시 후 다시 시도해 주세요.')
      } else {
        setMessage('업로드 실패 — 잠시 후 다시 시도해 주세요.')
      }
    }
  }

  return (
    <div
      role="dialog"
      aria-label="문서 추가"
      onClick={() => {
        if (status !== 'uploading') onClose()
      }}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-6"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-md overflow-hidden rounded-xl bg-white shadow-xl"
      >
        <header className="flex items-center justify-between border-b border-[#e5e7eb] bg-[#f8f9fa] px-6 py-4">
          <h3 className="text-base font-semibold text-[#111827]">문서 추가</h3>
          <button
            type="button"
            onClick={onClose}
            disabled={status === 'uploading'}
            aria-label="닫기"
            className="cursor-pointer rounded-lg p-1.5 text-[#6b7280] hover:bg-[#f3f4f6] disabled:cursor-not-allowed disabled:opacity-50"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="6" y1="6" x2="18" y2="18" />
              <line x1="6" y1="18" x2="18" y2="6" />
            </svg>
          </button>
        </header>
        <form onSubmit={handleSubmit} aria-label="문서 업로드" className="flex flex-col gap-4 p-6">
          <label className="flex flex-col gap-1">
            <span className="text-xs font-semibold text-[#6b7280]">저장 위치</span>
            <select
              aria-label="저장 위치"
              value={source}
              onChange={(e) => setSource(e.target.value as SourceType)}
              disabled={status === 'uploading'}
              className="h-10 rounded-lg border border-[#d1d5db] bg-white px-3 text-sm text-[#111827] outline-none focus:border-[#1d4ed8] focus:ring-1 focus:ring-[#1d4ed8] disabled:opacity-50"
            >
              <option value="onedrive">OneDrive</option>
              <option value="googledrive">Google Drive</option>
              <option value="slack">Slack</option>
            </select>
          </label>
          <div className="flex flex-col gap-1">
            <span className="text-xs font-semibold text-[#6b7280]">파일</span>
            <Dropzone
              onFiles={handleFiles}
              accept=".pdf,.docx,.pptx,.xlsx,.txt"
              ariaLabel="업로드 파일"
              disabled={status === 'uploading'}
              className="rounded-lg border-2 border-dashed border-[#d1d5db] bg-white p-6 text-center hover:border-[#9ca3af]"
              label={
                file ? (
                  <div>
                    <p className="text-sm font-semibold text-[#111827]">{file.name}</p>
                    <p className="mt-1 text-xs text-[#6b7280]">
                      {Math.round(file.size / 1024)} KB · 클릭/드랍으로 교체
                    </p>
                  </div>
                ) : (
                  <div>
                    <p className="text-sm font-semibold text-[#374151]">
                      파일을 드랍하거나 클릭해서 선택
                    </p>
                    <p className="mt-1 text-xs text-[#9ca3af]">
                      PDF · DOCX · PPTX · XLSX · TXT
                    </p>
                  </div>
                )
              }
            />
          </div>
          {status === 'uploading' && file ? (
            <UploadProgress file={file} />
          ) : null}
          {message ? (
            <p
              role={status === 'error' ? 'alert' : 'status'}
              className={`rounded-lg border px-3 py-2 text-xs ${
                status === 'error'
                  ? 'border-[#fecaca] bg-[#fef2f2] text-[#dc2626]'
                  : 'border-[#bbf7d0] bg-[#f0fdf4] text-[#166534]'
              }`}
            >
              {message}
            </p>
          ) : null}
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={onClose}
              disabled={status === 'uploading'}
              className="h-10 cursor-pointer rounded-lg border border-[#d1d5db] bg-white px-4 text-sm font-semibold text-[#374151] hover:bg-[#f3f4f6] disabled:cursor-not-allowed disabled:opacity-50"
            >
              {status === 'done' ? '닫기' : '취소'}
            </button>
            <button
              type="submit"
              disabled={file === null || status === 'uploading'}
              className="h-10 cursor-pointer rounded-lg bg-[#1d4ed8] px-5 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-[#1e40af] disabled:cursor-not-allowed disabled:opacity-40"
            >
              {status === 'uploading' ? '적재 중…' : '추가'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

/**
 * 업로드 + 적재(추출 + 청킹 + 임베딩 + 인덱싱) 진행 표시.
 * 예상 시간 — 파일 크기 기반: 5초 baseline + 1MB당 4초.
 */
function UploadProgress({ file }: { file: File }) {
  const sizeMB = file.size / (1024 * 1024)
  const estimatedMs = 5_000 + Math.round(sizeMB * 4_000)
  const progress = useTimedProgress(true, estimatedMs)
  const remainingSec = Math.max(
    0,
    Math.round((estimatedMs * (100 - progress)) / 100 / 1000),
  )
  return (
    <div className="flex items-center gap-3 rounded-lg border border-[#bfdbfe] bg-[#eff6ff] px-3 py-3">
      <CircularProgress
        value={progress}
        size={48}
        thickness={5}
        label={`${Math.floor(progress)}%`}
        color="#1d4ed8"
        ariaLabel="적재 진행률"
      />
      <div className="min-w-0 flex-1">
        <p className="truncate text-xs font-semibold text-[#1f2937]">{file.name}</p>
        <p className="text-[11px] text-[#6b7280]">
          추출 → 청킹 → 임베딩 → 인덱싱 · 남은 시간 약 {remainingSec}초
        </p>
      </div>
    </div>
  )
}
