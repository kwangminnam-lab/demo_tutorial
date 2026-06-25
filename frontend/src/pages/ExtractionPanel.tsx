/**
 * 필드 추출 패널 (IDP, ADR-024/025) — 사이드바 "필드 추출" 페이지(/extract)에서 사용.
 *
 * 파싱된 같은 파일을 재사용해(재업로드 없음) 스키마대로 값을 뽑고 근거(B-Box)를
 * 보여준다. 스키마는 **자동 제안→일부 수정→저장**이 기본 플로우 — 마스터가 "스키마 자동
 * 생성"으로 문서에서 필드를 제안받아 폼에서 고친 뒤 저장한다(수동 정의는 보조). 스키마
 * 관리는 마스터 전용 — 서버가 권위로 403, 프론트는 is_master로 UI만 게이팅.
 *
 * 추출 대상: 디지털 PDF(pymupdf) + 스캔/이미지(클러스터 PaddleOCR). 손글씨/스캔은
 * Gemini(VLM) 토글로도 처리(외부 전송). 비지원 포맷이면 안내만 표시한다.
 */

import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import type { ApiClient } from '../api/client'
import { ApiError } from '../api/client'
import type {
  ExtractResponse,
  ExtractedField,
  ExtractionSchema,
  SchemaField,
} from '../api/types'
import { extractResultToJsonl } from '../lib/jsonl'

export function ExtractionPanel({
  client,
  file,
  docType,
  onReset,
  onResult,
  onHoverEvidence,
}: {
  client: ApiClient
  file: File | null
  docType: string
  /** 초기화면(업로드)으로 — 부모가 파일을 비운다. 미지정이면 초기화면 버튼 숨김. */
  onReset?: () => void
  /** 추출 결과 변화 통지 — 부모(좌측 원본 미리보기)가 근거(B-Box) 프리뷰를 띄운다. */
  onResult?: (result: ExtractResponse | null) => void
  /** 필드 hover 시 강조할 근거 페이지 — 부모 좌측 프리뷰가 해당 페이지로 스크롤·강조. */
  onHoverEvidence?: (page: number | null) => void
}) {
  const [schemas, setSchemas] = useState<ExtractionSchema[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [isMaster, setIsMaster] = useState(false)
  const [showManager, setShowManager] = useState(false)
  // 편집 중인 기존 스키마(null이면 새로 만들기). 매니저는 이 값으로 prefill·저장분기.
  const [editing, setEditing] = useState<ExtractionSchema | null>(null)

  function openManager() {
    setEditing(null)
    setShowManager(true)
  }
  function openManagerEdit(schema: ExtractionSchema) {
    setEditing(schema)
    setShowManager(true)
  }
  function closeManager() {
    setShowManager(false)
    setEditing(null)
  }

  const selectedSchema = schemas.find((s) => s.id === selectedId) ?? null

  const [status, setStatus] = useState<'idle' | 'running' | 'done' | 'error'>('idle')
  const [result, setResult] = useState<ExtractResponse | null>(null)
  const [error, setError] = useState('')
  const abortRef = useRef<AbortController | null>(null)

  // 선택한 템플릿(스키마) 삭제 — 마스터 전용. 확인 후 삭제하고 목록 갱신.
  async function handleDeleteSchema() {
    if (selectedId === null) return
    if (!window.confirm(`템플릿 «${selectedSchema?.name ?? ''}» 을(를) 삭제할까요?`)) return
    try {
      await client.deleteExtractSchema(selectedId)
      setSelectedId(null)
      refreshSchemas()
    } catch (err) {
      window.alert(err instanceof ApiError ? err.message : '템플릿 삭제 실패')
    }
  }

  // 결과 보기 → 추출 폼으로(결과만 비움, 파일·스키마 유지).
  function backToForm() {
    setResult(null)
    setStatus('idle')
    setError('')
  }

  // PDF + 스캔 이미지(PNG/JPG 등) 추출 가능. 이미지는 텍스트 레이어가 없어 VLM 기본 ON.
  const IMAGE_TYPES = ['PNG', 'JPG', 'JPEG', 'WEBP', 'BMP', 'TIF', 'TIFF', 'GIF']
  const upper = docType.toUpperCase()
  const isImage = IMAGE_TYPES.includes(upper)
  const canExtract = upper === 'PDF' || isImage
  const [vlm, setVlm] = useState(false)
  // 이미지로 바뀌면 손글씨/스캔(VLM)을 기본 ON(이미지는 로컬 텍스트 추출 불가).
  useEffect(() => {
    setVlm(isImage)
  }, [isImage])

  const refreshSchemas = useCallback(() => {
    client
      .listExtractSchemas()
      .then((list) => {
        setSchemas(list)
        setSelectedId((prev) =>
          prev !== null && list.some((s) => s.id === prev) ? prev : (list[0]?.id ?? null),
        )
      })
      .catch(() => setSchemas([]))
  }, [client])

  useEffect(() => {
    refreshSchemas()
    client.me().then((m) => setIsMaster(m.is_master)).catch(() => setIsMaster(false))
  }, [client, refreshSchemas])

  // 파일이 바뀌면 이전 결과를 비운다(다른 문서 결과 잔존 방지).
  useEffect(() => {
    setResult(null)
    setStatus('idle')
    setError('')
  }, [file])

  // 결과 변화를 부모에 통지 — 부모가 좌측 원본 미리보기를 근거(B-Box) 프리뷰로 전환한다.
  useEffect(() => {
    onResult?.(result)
  }, [result, onResult])

  function handleRun(event: FormEvent) {
    event.preventDefault()
    if (!file || selectedId === null || status === 'running') return
    const controller = new AbortController()
    abortRef.current = controller
    setStatus('running')
    setError('')
    setResult(null)
    client
      .runExtraction(file, selectedId, { vlm, signal: controller.signal })
      .then((res) => {
        setResult(res)
        setStatus('done')
      })
      .catch((err) => {
        if (controller.signal.aborted) return
        setError(err instanceof ApiError ? err.message : '추출 실패')
        setStatus('error')
      })
  }

  return (
    <section
      aria-label="필드 추출"
      className="flex min-h-0 flex-col overflow-hidden rounded-lg border border-[#e5e7eb] bg-white"
    >
      <header className="flex items-center justify-between border-b border-[#e5e7eb] bg-[#f8fafc] px-4 py-2">
        <span className="text-[11px] font-bold uppercase tracking-wide text-[#1f2937]">
          {showManager ? (editing ? '템플릿 수정' : '템플릿 작성') : '필드 추출'}
        </span>
        {isMaster && !showManager && (
          <button
            type="button"
            onClick={openManager}
            className="rounded-md bg-[#1d4ed8] px-2 py-1 text-[11px] font-semibold text-white hover:bg-[#1e40af]"
          >
            템플릿 만들기
          </button>
        )}
        {showManager && (
          <button
            type="button"
            onClick={closeManager}
            aria-label="추출로 돌아가기"
            className="rounded-md border border-[#d1d5db] px-2 py-1 text-[11px] font-semibold text-[#374151] hover:bg-[#f3f4f6]"
          >
            ← 추출로
          </button>
        )}
      </header>

      {showManager ? (
        <SchemaManager
          client={client}
          editSchema={editing}
          defaultDocType={canExtract ? docType : ''}
          file={file}
          vlm={vlm}
          onChanged={refreshSchemas}
          onSaved={(saved) => {
            refreshSchemas()
            if (saved.id != null) setSelectedId(saved.id)
            closeManager()
          }}
        />
      ) : (
      <div className="flex-1 overflow-y-auto p-3">
        {!canExtract ? (
          <p className="py-6 text-center text-xs text-[#6b7280]">
            필드 추출은 PDF·스캔 이미지(PNG/JPG)만 지원합니다 (현재: {docType || '알 수 없음'}).
          </p>
        ) : schemas.length === 0 ? (
          <div className="py-6 text-center">
            <p className="mb-3 text-xs text-[#6b7280]">등록된 템플릿이 없습니다.</p>
            {isMaster ? (
              <button
                type="button"
                onClick={openManager}
                className="rounded-md bg-[#1d4ed8] px-4 py-2 text-sm font-semibold text-white hover:bg-[#1e40af]"
              >
                템플릿 만들기
              </button>
            ) : (
              <p className="text-xs text-[#92400e]">마스터에게 템플릿 생성을 요청하세요.</p>
            )}
          </div>
        ) : (
          <form onSubmit={handleRun} className="flex flex-col gap-2">
            <div className="flex items-center gap-2">
              <select
                aria-label="추출 스키마 선택"
                value={selectedId ?? ''}
                onChange={(e) => setSelectedId(Number.parseInt(e.target.value, 10))}
                className="min-w-0 flex-1 rounded-md border border-[#d1d5db] px-2 py-1.5 text-sm focus:border-[#1d4ed8] focus:outline-none"
              >
                {schemas.map((s) => (
                  <option key={s.id} value={s.id ?? ''}>
                    {s.name} · {s.fields.length}필드
                  </option>
                ))}
              </select>
              {isMaster && selectedSchema && (
                <button
                  type="button"
                  onClick={() => openManagerEdit(selectedSchema)}
                  aria-label="선택한 템플릿 수정"
                  className="rounded-md border border-[#d1d5db] px-2 py-1.5 text-sm font-semibold text-[#374151] hover:bg-[#f3f4f6]"
                >
                  수정
                </button>
              )}
              {isMaster && selectedSchema && (
                <button
                  type="button"
                  onClick={handleDeleteSchema}
                  aria-label="선택한 템플릿 삭제"
                  className="rounded-md border border-[#fca5a5] px-2 py-1.5 text-sm font-semibold text-[#b91c1c] hover:bg-[#fef2f2]"
                >
                  삭제
                </button>
              )}
              <button
                type="submit"
                disabled={!file || selectedId === null || status === 'running'}
                className="rounded-md bg-[#1d4ed8] px-3 py-1.5 text-sm font-semibold text-white hover:bg-[#1e40af] disabled:opacity-40"
              >
                {status === 'running' ? '추출 중…' : '추출'}
              </button>
            </div>
            <label className="flex items-center gap-1.5 text-xs text-[#6b7280]">
              <input
                type="checkbox"
                aria-label="손글씨/스캔 모드"
                checked={vlm}
                onChange={(e) => setVlm(e.target.checked)}
              />
              손글씨/스캔 모드
            </label>
          </form>
        )}

        {/* 선택한 템플릿 내용을 표로 미리 보여준다(추출 전). */}
        {canExtract && selectedSchema && !result && (
          <SchemaFieldsTable schema={selectedSchema} />
        )}

        {error && (
          <div role="alert" className="mt-2 rounded-md bg-[#fef2f2] px-3 py-2 text-xs text-[#b91c1c]">
            {error}
          </div>
        )}

        {result && (
          <ExtractionResultView
            result={result}
            client={client}
            onBack={backToForm}
            onReset={onReset}
            onHoverEvidence={onHoverEvidence}
          />
        )}
      </div>
      )}
    </section>
  )
}

/** 선택한 템플릿의 필드를 읽기 전용 표로 보여준다(추출 전 미리보기). */
function SchemaFieldsTable({ schema }: { schema: ExtractionSchema }) {
  return (
    <div className="mt-3">
      <p className="mb-1 text-[10px] font-bold uppercase tracking-wide text-[#6b7280]">
        템플릿 «{schema.name}» 필드
      </p>
      <div className="overflow-x-auto rounded-md border border-[#e5e7eb]">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[#e5e7eb] bg-[#f8fafc] text-left text-[11px] font-semibold uppercase tracking-wide text-[#6b7280]">
              <th className="px-2 py-1.5">Field name</th>
              <th className="px-2 py-1.5">Description</th>
              <th className="w-24 px-2 py-1.5">Data type</th>
              <th className="w-14 px-2 py-1.5 text-center">Multi</th>
            </tr>
          </thead>
          <tbody>
            {schema.fields.map((f, i) => (
              <tr key={i} className="border-b border-[#f3f4f6] last:border-0">
                <td className="px-2 py-1.5 font-medium text-[#374151]">{f.key}</td>
                <td className="px-2 py-1.5 text-[#6b7280]">{f.description || '—'}</td>
                <td className="px-2 py-1.5 text-[#6b7280]">{f.type}</td>
                <td className="px-2 py-1.5 text-center text-[#6b7280]">{f.multi ? '✓' : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function ExtractionResultView({
  result,
  client,
  onBack,
  onReset,
  onHoverEvidence,
}: {
  result: ExtractResponse
  client: ApiClient
  /** 결과 보기 이전(추출 폼)으로. */
  onBack: () => void
  /** 초기화면(업로드)으로. 미지정이면 버튼 숨김. */
  onReset?: () => void
  /** 필드 hover 시 강조할 근거 페이지 — 좌측 원본 미리보기가 그 페이지로 강조·스크롤. */
  onHoverEvidence?: (page: number | null) => void
}) {
  // 값(value)은 사용자가 보정할 수 있다 — AI OCR 오인식 교정. 키·신뢰도·근거는 불변.
  // 다른 문서로 결과가 바뀌면 편집 상태를 새 결과로 재동기화한다.
  const [editedValues, setEditedValues] = useState<(string | null)[]>(() =>
    result.result.fields.map((f) => f.value),
  )
  useEffect(() => {
    setEditedValues(result.result.fields.map((f) => f.value))
  }, [result])

  const [saving, setSaving] = useState(false)
  const [saveMsg, setSaveMsg] = useState<string | null>(null)

  const filled = editedValues.filter((v) => v !== null && v !== '').length
  const review = result.result.fields.filter((f) => f.needs_review).length

  // 보정값을 반영한 결과 — 다운로드·서버 저장에 공통 사용(화면=파일 일치).
  function correctedResult(): ExtractResponse['result'] {
    return {
      ...result.result,
      fields: result.result.fields.map((f, i) => ({ ...f, value: editedValues[i] })),
    }
  }

  function setValue(index: number, value: string) {
    setEditedValues((prev) => prev.map((v, i) => (i === index ? (value === '' ? null : value) : v)))
    setSaveMsg(null)
  }

  // 추출 결과를 JSON 파일로 저장 — 키-값 맵 + 상세(근거/신뢰도) 함께(보정값 반영). blob 다운로드.
  function downloadJson() {
    const corrected = correctedResult()
    const data = {
      doc_id: corrected.doc_id,
      schema_id: corrected.schema_id,
      values: Object.fromEntries(corrected.fields.map((f) => [f.key, f.value])),
      fields: corrected.fields,
    }
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
    triggerBlobDownload(blob, `extraction_${corrected.doc_id.slice(0, 8)}.json`)
  }

  // 보정값을 그대로 필드당 1줄 NDJSON으로 저장 — 추출 재실행 없이 화면과 일치(과금·비결정 회피).
  function downloadJsonl() {
    const blob = new Blob([extractResultToJsonl(correctedResult())], {
      type: 'application/x-ndjson;charset=utf-8',
    })
    triggerBlobDownload(blob, `extraction_${result.result.doc_id.slice(0, 8)}.jsonl`)
  }

  // 보정 결과를 서버 export_root(공유 PVC)에 영속 — 추출 재실행 없이 화면 값을 그대로 저장.
  async function saveCorrected() {
    setSaving(true)
    setSaveMsg(null)
    try {
      const resp = await client.exportCorrectedExtraction(correctedResult())
      if (resp.export_path) {
        setSaveMsg(`저장됨: ${resp.export_path}`)
      } else {
        setSaveMsg(`저장 실패: ${resp.export_error ?? '알 수 없는 오류'}`)
      }
    } catch (error) {
      setSaveMsg(error instanceof ApiError ? `저장 실패: ${error.message}` : '저장 실패')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="mt-3">
      {/* 네비게이션 — 결과 이전(추출 폼) / 초기화면(업로드) */}
      <div className="mb-2 flex items-center gap-2 text-xs">
        <button
          type="button"
          onClick={onBack}
          className="rounded-md border border-[#d1d5db] px-2 py-1 font-semibold text-[#374151] hover:bg-[#f3f4f6]"
        >
          ← 결과 이전으로
        </button>
        {onReset && (
          <button
            type="button"
            onClick={onReset}
            className="rounded-md border border-[#d1d5db] px-2 py-1 font-semibold text-[#374151] hover:bg-[#f3f4f6]"
          >
            초기화면으로
          </button>
        )}
      </div>

      <div className="mb-2 flex items-center justify-between gap-2 text-xs">
        <div className="flex items-center gap-2">
          <span className="rounded bg-[#eef2ff] px-2 py-0.5 font-bold text-[#1d4ed8]">
            {filled}/{result.result.fields.length} 추출
          </span>
          {review > 0 && (
            <span className="rounded bg-[#fef2f2] px-2 py-0.5 font-bold text-[#b91c1c]">
              {review} 확인 필요
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={saveCorrected}
            disabled={saving}
            className="rounded-md border border-[#1d4ed8] bg-[#1d4ed8] px-2 py-1 font-semibold text-white hover:bg-[#1e40af] disabled:opacity-50"
          >
            {saving ? '저장 중…' : '수정 저장'}
          </button>
          <button
            type="button"
            onClick={downloadJson}
            className="rounded-md border border-[#d1d5db] px-2 py-1 font-semibold text-[#374151] hover:bg-[#f3f4f6]"
          >
            JSON 저장
          </button>
          <button
            type="button"
            onClick={downloadJsonl}
            className="rounded-md border border-[#d1d5db] px-2 py-1 font-semibold text-[#374151] hover:bg-[#f3f4f6]"
          >
            JSONL 저장
          </button>
        </div>
      </div>

      {saveMsg && (
        <p
          className={`mb-2 text-xs ${
            saveMsg.startsWith('저장됨') ? 'text-[#15803d]' : 'text-[#b91c1c]'
          }`}
        >
          {saveMsg}
        </p>
      )}

      <table className="w-full text-sm">
        <thead className="text-xs text-[#6b7280]">
          <tr>
            <th className="px-2 py-1 text-left font-semibold">필드</th>
            <th className="px-2 py-1 text-left font-semibold">값 (수정 가능)</th>
            <th className="px-2 py-1 text-right font-semibold">신뢰도</th>
          </tr>
        </thead>
        <tbody>
          {result.result.fields.map((field, index) => (
            <FieldRow
              key={field.key}
              field={field}
              value={editedValues[index] ?? ''}
              onChange={(v) => setValue(index, v)}
              onHover={() => onHoverEvidence?.(field.page ?? null)}
              onLeave={() => onHoverEvidence?.(null)}
            />
          ))}
        </tbody>
      </table>
      {/* 근거(B-Box) 프리뷰는 좌측 '원본 미리보기' 칸에 표시된다(필드 hover로 강조). */}
    </div>
  )
}

function FieldRow({
  field,
  value,
  onChange,
  onHover,
  onLeave,
}: {
  field: ExtractedField
  value: string
  onChange: (value: string) => void
  onHover: () => void
  onLeave: () => void
}) {
  return (
    <tr
      onMouseEnter={onHover}
      onMouseLeave={onLeave}
      className="border-t border-[#f3f4f6] hover:bg-[#f9fafb]"
    >
      <td className="px-2 py-1.5 align-top font-medium text-[#374151]">
        {field.key}
        {field.needs_review && (
          <span className="ml-1 rounded bg-[#fef2f2] px-1 py-0.5 text-[10px] font-bold text-[#b91c1c]">
            확인
          </span>
        )}
        {field.page != null && (
          <span className="ml-1 text-[10px] text-[#9ca3af]">p{field.page}</span>
        )}
      </td>
      <td className="px-2 py-1.5 align-top text-[#111827]">
        <input
          type="text"
          aria-label={`${field.key} 값`}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="— 없음"
          className={`w-full rounded border bg-white px-1.5 py-1 text-sm text-[#111827] focus:border-[#1d4ed8] focus:outline-none focus:ring-1 focus:ring-[#1d4ed8] ${
            field.needs_review ? 'border-[#fca5a5]' : 'border-[#e5e7eb]'
          }`}
        />
      </td>
      <td className="px-2 py-1.5 text-right align-top tabular-nums text-[#6b7280]">
        {field.confidence == null ? '—' : `${Math.round(field.confidence * 100)}%`}
      </td>
    </tr>
  )
}

/** Blob을 첨부 파일로 저장(앵커 클릭) — JSON/JSONL 다운로드 공용. */
function triggerBlobDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1_000)
}

// ── 템플릿 만들기 (인라인, 마스터) ───────────────────────────────────────
// 데이터 타입 — 프로그래밍식 표기. boolean은 동의/비동의 같은 예/아니오 필드용.
const FIELD_TYPES = ['String', 'int', 'float', 'boolean', 'date'] as const

function SchemaManager({
  client,
  editSchema = null,
  defaultDocType,
  file,
  vlm,
  onChanged,
  onSaved,
}: {
  client: ApiClient
  editSchema?: ExtractionSchema | null
  defaultDocType: string
  file: File | null
  vlm: boolean
  onChanged: () => void
  onSaved?: (saved: ExtractionSchema) => void
}) {
  // editSchema 가 있으면 그 값으로 prefill(수정 모드). 매니저는 열 때마다 remount 되므로
  // 초기 state 로 충분(별도 sync useEffect 불요).
  const [name, setName] = useState(editSchema?.name ?? '')
  const [docType, setDocType] = useState(editSchema?.doc_type ?? defaultDocType)
  const [fields, setFields] = useState<SchemaField[]>(
    editSchema && editSchema.fields.length > 0
      ? editSchema.fields
      : [{ key: '', type: 'String', required: false, multi: false }],
  )
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)

  function setField(i: number, patch: Partial<SchemaField>) {
    setFields((prev) => prev.map((f, idx) => (idx === i ? { ...f, ...patch } : f)))
  }

  async function handleAuto() {
    if (!file) {
      setMsg('자동 생성은 업로드된 파일이 필요합니다 (먼저 문서를 올리세요)')
      return
    }
    setBusy(true)
    setMsg('템플릿 자동 생성 중…')
    try {
      const schema = await client.autoExtractSchema(file, {
        docType: docType || undefined,
        vlm,
      })
      setName(schema.name)
      if (schema.doc_type) setDocType(schema.doc_type)
      if (schema.fields.length) setFields(schema.fields)
      setMsg(`${schema.fields.length}개 필드 제안됨 — 확인·수정 후 저장`)
    } catch (err) {
      setMsg(err instanceof ApiError ? err.message : '자동 생성 실패')
    } finally {
      setBusy(false)
    }
  }

  async function handleSave(event: FormEvent) {
    event.preventDefault()
    const clean = fields.filter((f) => f.key.trim())
    if (!name.trim() || clean.length === 0) {
      setMsg('이름과 최소 1개 필드가 필요합니다')
      return
    }
    setBusy(true)
    try {
      const payload = {
        name: name.trim(),
        doc_type: docType.trim() || null,
        fields: clean,
        auto_generated: false,
      }
      // 수정 모드면 기존 id 로 PUT, 아니면 새로 생성. (서버는 본문 id 무시·경로값 사용)
      const saved =
        editSchema?.id != null
          ? await client.updateExtractSchema(editSchema.id, payload)
          : await client.createExtractSchema(payload)
      if (!editSchema) {
        setName('')
        setFields([{ key: '', type: 'String', required: false, multi: false }])
      }
      setMsg(editSchema ? '템플릿 수정됨' : '템플릿 저장됨')
      onChanged()
      // 저장/수정 직후 그 스키마를 선택하고 모달을 닫는다(저장→선택→추출 흐름).
      onSaved?.(saved)
    } catch (err) {
      setMsg(err instanceof ApiError ? err.message : editSchema ? '수정 실패' : '저장 실패')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div aria-label="템플릿 작성" className="flex min-h-0 flex-1 flex-col overflow-y-auto p-4">
        <form onSubmit={handleSave}>
          <h3 className="mb-2 text-sm font-semibold text-[#374151]">
            {editSchema ? '템플릿 수정' : '새 템플릿'}
          </h3>
          <div className="mb-3 flex gap-2">
            <input
              type="text"
              aria-label="템플릿 이름"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="이름 (예: 계약서)"
              className="min-w-0 flex-1 rounded-md border border-[#d1d5db] px-3 py-1.5 text-sm focus:border-[#1d4ed8] focus:outline-none"
            />
            <input
              type="text"
              aria-label="문서 종류"
              value={docType}
              onChange={(e) => setDocType(e.target.value)}
              placeholder="문서 종류 (선택)"
              className="w-40 rounded-md border border-[#d1d5db] px-3 py-1.5 text-sm focus:border-[#1d4ed8] focus:outline-none"
            />
          </div>

          <button
            type="button"
            onClick={handleAuto}
            disabled={busy || !file}
            className="mb-3 w-full rounded-md border border-dashed border-[#c7d2fe] bg-[#eef2ff] px-3 py-2 text-xs font-semibold text-[#1d4ed8] hover:bg-[#e0e7ff] disabled:opacity-40"
          >
            ⚡ 템플릿 자동생성
          </button>

          {/* 필드 표 — 스프레드시트형 그리드(셀 구분선 + 테두리 없는 입력) */}
          <div className="mb-4 overflow-x-auto rounded-md border border-[#d1d5db]">
            <table className="w-full border-collapse text-sm">
              <thead>
                <tr className="bg-[#f3f4f6] text-left text-[11px] font-bold uppercase tracking-wide text-[#6b7280]">
                  <th className="border-b border-r border-[#e5e7eb] px-2 py-1.5">Field name</th>
                  <th className="border-b border-r border-[#e5e7eb] px-2 py-1.5">Description</th>
                  <th className="w-28 border-b border-r border-[#e5e7eb] px-2 py-1.5">Data type</th>
                  <th className="w-14 border-b border-r border-[#e5e7eb] px-2 py-1.5 text-center">Multi</th>
                  <th className="w-9 border-b border-[#e5e7eb] px-1 py-1.5" aria-label="제거" />
                </tr>
              </thead>
              <tbody>
                {fields.map((f, i) => (
                  <tr key={i} className="border-b border-[#e5e7eb] last:border-0">
                    <td className="border-r border-[#e5e7eb] p-0">
                      <input
                        type="text"
                        aria-label={`필드 ${i + 1} 이름`}
                        value={f.key}
                        onChange={(e) => setField(i, { key: e.target.value })}
                        placeholder="필드명"
                        className="w-full border-0 bg-transparent px-2 py-1.5 text-sm focus:bg-[#eff6ff] focus:outline-none"
                      />
                    </td>
                    <td className="border-r border-[#e5e7eb] p-0">
                      <input
                        type="text"
                        aria-label={`필드 ${i + 1} 설명`}
                        value={f.description ?? ''}
                        onChange={(e) => setField(i, { description: e.target.value })}
                        placeholder="프롬프트/지침 (예: 하단 인장 옆 글자)"
                        className="w-full border-0 bg-transparent px-2 py-1.5 text-sm focus:bg-[#eff6ff] focus:outline-none"
                      />
                    </td>
                    <td className="border-r border-[#e5e7eb] p-0">
                      <select
                        aria-label={`필드 ${i + 1} 타입`}
                        value={f.type}
                        onChange={(e) => setField(i, { type: e.target.value })}
                        className="w-full border-0 bg-transparent px-2 py-1.5 text-sm focus:bg-[#eff6ff] focus:outline-none"
                      >
                        {FIELD_TYPES.map((t) => (
                          <option key={t} value={t}>
                            {t}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className="border-r border-[#e5e7eb] p-0 text-center">
                      <input
                        type="checkbox"
                        aria-label={`필드 ${i + 1} Multi`}
                        checked={f.multi ?? false}
                        onChange={(e) => setField(i, { multi: e.target.checked })}
                      />
                    </td>
                    <td className="p-0 text-center">
                      <button
                        type="button"
                        onClick={() => setFields((prev) => prev.filter((_, idx) => idx !== i))}
                        aria-label={`필드 ${i + 1} 제거`}
                        className="px-1.5 py-1.5 text-sm text-[#9ca3af] hover:text-[#b91c1c]"
                      >
                        ✕
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <td colSpan={5} className="border-t border-[#e5e7eb] p-0">
                    <button
                      type="button"
                      onClick={() =>
                        setFields((prev) => [
                          ...prev,
                          { key: '', type: 'String', required: false, multi: false },
                        ])
                      }
                      className="w-full px-2 py-1.5 text-left text-xs font-semibold text-[#1d4ed8] hover:bg-[#eef2ff]"
                    >
                      + 필드 추가
                    </button>
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>

          {msg && <p className="mb-3 text-xs text-[#6b7280]">{msg}</p>}

          <div className="flex justify-end">
            <button
              type="submit"
              disabled={busy}
              className="rounded-md bg-[#1d4ed8] px-4 py-2 text-sm font-semibold text-white hover:bg-[#1e40af] disabled:opacity-40"
            >
              {editSchema ? '수정 저장' : '템플릿 저장'}
            </button>
          </div>
        </form>
    </div>
  )
}
