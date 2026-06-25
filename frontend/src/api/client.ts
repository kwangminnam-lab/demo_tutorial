/**
 * 백엔드 `/v1` 타입 안전 클라이언트.
 *
 * 경로는 `/v1/...`·`/healthz`로 쓰되, `apiUrl()`로 감싼다. `VITE_API_BASE_URL`이
 * 설정되면(예: GitHub Pages처럼 백엔드가 **다른 origin**일 때) 그 절대 URL을 prefix
 * 하고, 미설정(dev/동일 출처)이면 상대경로 그대로 — Vite proxy/동일 출처로 CORS 회피.
 * 인증 토큰은 주입된 `TokenProvider`로 받아 `Authorization` 헤더에만 싣는다(URL·바디·
 * 로그 미노출).
 */

import type {
  Answer,
  DiffResult,
  ExtractExportResult,
  ExtractionResult,
  ExtractionSchema,
  ExtractResponse,
  Health,
  ParseResponse,
  SearchHit,
  SourceType,
} from './types'

// 백엔드 API origin prefix. 미설정이면 ''(상대경로). 무료 배포(ADR-019): 프론트는
// GitHub Pages, 백엔드는 별도 호스트 → 빌드 시 VITE_API_BASE_URL로 그 호스트 지정.
const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/+$/, '')

/** 상대 API 경로를 (필요 시 절대 origin을 붙여) 최종 요청 URL로 만든다. */
export const apiUrl = (path: string): string => `${API_BASE}${path}`

/** 소스별 카운트 facets — 대시보드 커넥터 통계 + 검색 분포. */
export interface SourceFacets {
  total: number
  by_source: Record<string, number>
}

/** 적재 결과 — 백엔드 `IngestReport`(요약 필드만 미러). */
export interface IngestReport {
  ok: boolean
  doc_id?: string | null
  chunk_count?: number
  error?: string | null
}

/** 비동기 적재 작업 생애주기. */
export type IngestJobStatus = 'pending' | 'running' | 'done' | 'error'

/** 업로드 접수(202) 응답 — job_id로 상태를 폴링한다. */
export interface IngestJobAccepted {
  job_id: string
  status: IngestJobStatus
  filename: string
}

/** 적재 작업 상태 — done이면 report, error면 사유. */
export interface IngestJobState {
  job_id: string
  filename: string
  status: IngestJobStatus
  report?: IngestReport | null
  error?: string | null
}

/** 폴링 간격(ms)과 총 대기 상한(ms). */
const INGEST_POLL_INTERVAL_MS = 1500
const INGEST_POLL_TIMEOUT_MS = 10 * 60_000

const sleep = (ms: number): Promise<void> =>
  new Promise((resolve) => setTimeout(resolve, ms))

/** 인증 토큰 주입자 — 토큰을 반환하거나 미인증이면 null. */
export type TokenProvider = () => string | null

/** SSE 종료 센티넬 (OpenAI/LibreChat 호환 관례) — `data: [DONE]`. */
const SSE_DONE = '[DONE]'

export interface SearchOptions {
  source?: SourceType
  topK?: number
  docType?: string
  days?: number
  dateFrom?: string  // YYYY-MM-DD
  dateTo?: string    // YYYY-MM-DD
}

/** 현재 사용자 컨텍스트 — 사이드바 프로필 표시용. 토큰은 포함하지 않음. */
export interface Me {
  user_id: string
  department: string
  access_level: number
  role: string
  /** 마스터(관리자) 여부 — API키·커넥터·멤버 관리 UI 게이팅 기준 (ADR-017). */
  is_master: boolean
}

/** 로그인 응답 — 세션 토큰 + 역할. 비밀번호/해시 없음. */
export interface LoginResult {
  token: string
  user_id: string
  role: string
  is_master: boolean
}

/** 멤버 목록 항목 — 비밀번호/해시 미포함(서버가 제외). */
export interface Member {
  id: number
  email: string
  department: string
  access_level: number
  is_master: boolean
}

/** LLM 모델 식별자 — 사내 단일 LLM(gpt-oss-120b 게이트웨이). (qwen3 듀얼 제거됨.) */
export type LlmProvider = 'gemma'

export interface RagOptions {
  topK?: number
  /** 모델 선택(현재 gpt-oss 단독). 사내 엔드포인트라 키 불요. */
  provider?: LlmProvider
  /** 비활성 커넥터 — 결과에서 제외 (검색·RAG 공통). */
  disabledSources?: SourceType[]
}

export interface RagStreamOptions extends RagOptions {
  signal?: AbortSignal
}

/** 비-2xx 응답을 감싸는 에러 — 상태코드 + (있으면) 본문 detail. 토큰은 절대 미포함. */
export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export interface ApiClient {
  search(q: string, options?: SearchOptions): Promise<SearchHit[]>
  ragAnswer(query: string, options?: RagOptions): Promise<Answer>
  ragStream(
    query: string,
    onChunk: (chunk: string) => void,
    options?: RagStreamOptions,
  ): Promise<void>
  diff(docIdA: string, docIdB: string): Promise<DiffResult>
  /** 업로드 두 파일을 즉석 비교 — 인덱스 미등록 자료용. 바이트는 서버에 영속되지 않음. */
  diffUpload(fileA: File, fileB: File, options?: { signal?: AbortSignal }): Promise<DiffResult>
  /** 단일 파일 파싱 — HTML/JSON 미리보기 + 다운로드용. 적재 X. */
  parseUpload(file: File, options?: { signal?: AbortSignal }): Promise<ParseResponse>
  /** 단일 파일을 파싱해 JSONL(섹션당 1줄, NDJSON) 원문을 받는다 — `.jsonl` 다운로드 + 구조(heading) 뷰 공용. */
  parseJsonl(file: File, options?: { signal?: AbortSignal }): Promise<string>
  /** 소스별 카운트 facets — q 미지정 시 권한 내 전체 코퍼스 통계. */
  facets(q?: string): Promise<SourceFacets>
  /** 파일 업로드 적재 — 지정 커넥터(source)에 영속 저장 + 색인. */
  ingestUpload(file: File, source: SourceType): Promise<IngestReport>
  health(): Promise<Health>
  /** 현재 사용자 컨텍스트 — 프로필 표시용. */
  me(): Promise<Me>
  /** 이메일+비밀번호 로그인 — 서명 세션 토큰 발급(ADR-017). 인증 헤더 불요(공개). */
  login(email: string, password: string): Promise<LoginResult>
  /** 멤버 목록 (마스터 전용 — 멤버 호출 시 403). */
  listMembers(): Promise<Member[]>
  /** 멤버 추가 (마스터 전용) — 이메일+비밀번호 생성. */
  addMember(email: string, password: string, department?: string): Promise<Member>
  /** 멤버 삭제 (마스터 전용) — 마스터 계정은 삭제 불가. */
  deleteMember(id: number): Promise<void>
  /** 파일 원본을 blob으로 가져온다(권한 헤더 자동). 다운로드/inline 표시에 공통 사용. */
  fetchFile(docId: string, download?: boolean): Promise<{ blob: Blob; filename: string }>
  /** 문서 삭제 (마스터 전용) — PG·Neo4j 일괄 삭제. 멤버 호출 시 403. */
  deleteFile(docId: string): Promise<void>
  /** 추출 스키마 목록 (인증). */
  listExtractSchemas(): Promise<ExtractionSchema[]>
  /** 추출 스키마 생성 (마스터 전용). */
  createExtractSchema(schema: ExtractionSchema): Promise<ExtractionSchema>
  /** 기존 추출 스키마 수정 (마스터 전용). 없으면 404. */
  updateExtractSchema(schemaId: number, schema: ExtractionSchema): Promise<ExtractionSchema>
  /** 추출 스키마 삭제 (마스터 전용). */
  deleteExtractSchema(schemaId: number): Promise<void>
  /** 업로드 문서에서 스키마 자동 제안 (마스터 전용, 미영속). vlm=손글씨/스캔(Gemini). */
  autoExtractSchema(
    file: File,
    options?: { docType?: string; name?: string; vlm?: boolean; signal?: AbortSignal },
  ): Promise<ExtractionSchema>
  /** PDF를 스키마(schema_id)로 추출 — 값+근거 bbox+근거 PNG (인증). vlm=손글씨/스캔(Gemini). */
  runExtraction(
    file: File,
    schemaId: number,
    options?: { vlm?: boolean; signal?: AbortSignal },
  ): Promise<ExtractResponse>
  /** doc_id의 추출 결과 이력 (인증). */
  listExtractResults(docId: string): Promise<ExtractionResult[]>
  /** 사용자가 보정한 추출 결과를 export_root(공유 PVC)에 JSONL로 영속 (인증). 추출 재실행 X. */
  exportCorrectedExtraction(result: ExtractionResult): Promise<ExtractExportResult>
}

export function createClient(getToken: TokenProvider): ApiClient {
  // Authorization은 토큰이 있을 때만 싣는다(없으면 헤더 자체를 생략 → 백엔드가 401).
  function authHeaders(base?: HeadersInit): Headers {
    const headers = new Headers(base)
    const token = getToken()
    if (token) {
      headers.set('Authorization', `Bearer ${token}`)
    }
    return headers
  }

  async function getJson<T>(path: string): Promise<T> {
    const response = await ensureOk(await fetch(apiUrl(path), { headers: authHeaders() }))
    return (await response.json()) as T
  }

  async function postJson<T>(path: string, body: unknown): Promise<T> {
    const response = await ensureOk(
      await fetch(apiUrl(path), {
        method: 'POST',
        headers: authHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(body),
      }),
    )
    return (await response.json()) as T
  }

  async function putJson<T>(path: string, body: unknown): Promise<T> {
    const response = await ensureOk(
      await fetch(apiUrl(path), {
        method: 'PUT',
        headers: authHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(body),
      }),
    )
    return (await response.json()) as T
  }

  return {
    search(q, options = {}) {
      const params = new URLSearchParams({ q })
      if (options.source) params.set('source', options.source)
      if (options.topK !== undefined) params.set('top_k', String(options.topK))
      if (options.docType) params.set('doc_type', options.docType)
      if (options.days !== undefined) params.set('days', String(options.days))
      if (options.dateFrom) params.set('date_from', options.dateFrom)
      if (options.dateTo) params.set('date_to', options.dateTo)
      return getJson<SearchHit[]>(`/v1/search?${params.toString()}`)
    },

    ragAnswer(query, options = {}) {
      // 출처 보강 전용: LLM 재생성 없이 citations·grounded만 받는다(인용 표시 지연 제거).
      // 본문은 ragStream이 권위 — 여기 text는 비어 있다.
      const headers = authHeaders({ 'Content-Type': 'application/json' })
      return fetch(apiUrl('/v1/rag?stream=false&citations_only=true'), {
        method: 'POST',
        headers,
        body: JSON.stringify(ragBody(query, options)),
      }).then(async (response) => {
        const ok = await ensureOk(response)
        return (await ok.json()) as Answer
      })
    },

    async ragStream(query, onChunk, options = {}) {
      // 백엔드 기본은 SSE 스트리밍(stream 파라미터 생략 → true).
      const headers = authHeaders({ 'Content-Type': 'application/json' })
      const response = await ensureOk(
        await fetch(apiUrl('/v1/rag'), {
          method: 'POST',
          headers,
          body: JSON.stringify(ragBody(query, options)),
          signal: options.signal,
        }),
      )
      await readSse(response, onChunk)
    },

    diff(docIdA, docIdB) {
      return postJson<DiffResult>('/v1/diff', {
        doc_id_a: docIdA,
        doc_id_b: docIdB,
      })
    },

    facets(q) {
      const params = new URLSearchParams()
      if (q !== undefined && q !== '') {
        params.set('q', q)
      }
      const qs = params.toString()
      return getJson<SourceFacets>(`/v1/search/facets${qs ? `?${qs}` : ''}`)
    },

    async ingestUpload(file, source) {
      // 비동기 적재: 업로드는 202(job_id)로 즉시 접수되고 색인은 백그라운드에서 진행.
      // 클라가 상태를 폴링해 종료(done/error)되면 최종 리포트를 돌려준다(UI는 불변 —
      // 여전히 Promise<IngestReport>). 크기/형식 오류(413/415)는 POST에서 즉시 던진다.
      const form = new FormData()
      form.append('file', file, file.name)
      form.append('source', source)
      const response = await ensureOk(
        await fetch(apiUrl('/v1/ingest/upload'), {
          method: 'POST',
          headers: authHeaders(),
          body: form,
        }),
      )
      const accepted = (await response.json()) as IngestJobAccepted
      const deadline = Date.now() + INGEST_POLL_TIMEOUT_MS
      for (;;) {
        const job = await getJson<IngestJobState>(
          `/v1/ingest/jobs/${encodeURIComponent(accepted.job_id)}`,
        )
        if (job.status === 'done') {
          return job.report ?? { ok: true }
        }
        if (job.status === 'error') {
          return { ok: false, error: job.error ?? '적재 실패' }
        }
        if (Date.now() > deadline) {
          return { ok: false, error: '적재 시간이 초과되었습니다. 잠시 후 다시 확인해 주세요.' }
        }
        await sleep(INGEST_POLL_INTERVAL_MS)
      }
    },

    async parseUpload(file, options = {}) {
      const form = new FormData()
      form.append('file', file, file.name)
      const response = await ensureOk(
        await fetch(apiUrl('/v1/parse/upload'), {
          method: 'POST',
          headers: authHeaders(),
          body: form,
          signal: options.signal,
        }),
      )
      return (await response.json()) as ParseResponse
    },

    async parseJsonl(file, options = {}) {
      // 미리보기용 /parse/upload와 별개 — 같은 IR을 줄단위(NDJSON)로 받아 다운로드·구조 뷰에 쓴다.
      const form = new FormData()
      form.append('file', file, file.name)
      const response = await ensureOk(
        await fetch(apiUrl('/v1/parse/jsonl'), {
          method: 'POST',
          headers: authHeaders(),
          body: form,
          signal: options.signal,
        }),
      )
      return await response.text()
    },

    async diffUpload(fileA, fileB, options = {}) {
      const form = new FormData()
      form.append('file_a', fileA, fileA.name)
      form.append('file_b', fileB, fileB.name)
      // FormData 사용 시 Content-Type을 직접 지정하지 않는다(브라우저가 boundary 포함해서 설정).
      const response = await ensureOk(
        await fetch(apiUrl('/v1/diff/upload'), {
          method: 'POST',
          headers: authHeaders(),
          body: form,
          signal: options.signal,
        }),
      )
      return (await response.json()) as DiffResult
    },

    health() {
      return getJson<Health>('/healthz')
    },

    me() {
      return getJson<Me>('/v1/me')
    },

    async login(email, password) {
      // 공개 라우트 — 인증 헤더 없이 호출. 비밀번호는 본문으로만 전송(URL 미포함).
      const response = await ensureOk(
        await fetch(apiUrl('/v1/auth/login'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email, password }),
        }),
      )
      return (await response.json()) as LoginResult
    },

    listMembers() {
      return getJson<Member[]>('/v1/members')
    },

    addMember(email, password, department = 'default') {
      return postJson<Member>('/v1/members', { email, password, department })
    },

    async deleteMember(id) {
      // 204 No Content — 본문 파싱하지 않는다.
      await ensureOk(
        await fetch(apiUrl(`/v1/members/${id}`), { method: 'DELETE', headers: authHeaders() }),
      )
    },

    async fetchFile(docId, download = false) {
      // 토큰을 헤더로만 전송 — URL/쿼리에 싣지 않는다(브라우저 히스토리·로그 누설 방지).
      // 응답을 blob으로 받아 호출자가 URL.createObjectURL로 열거나 다운로드한다.
      const response = await ensureOk(
        await fetch(apiUrl(`/v1/files/${encodeURIComponent(docId)}?download=${download ? '1' : '0'}`), {
          headers: authHeaders(),
        }),
      )
      const blob = await response.blob()
      const filename = filenameFromHeader(response.headers.get('content-disposition')) ?? docId
      return { blob, filename }
    },

    async deleteFile(docId) {
      // 204 No Content — 본문 파싱하지 않는다. 마스터 아니면 서버가 403.
      await ensureOk(
        await fetch(apiUrl(`/v1/files/${encodeURIComponent(docId)}`), {
          method: 'DELETE',
          headers: authHeaders(),
        }),
      )
    },

    listExtractSchemas() {
      return getJson<ExtractionSchema[]>('/v1/extract/schemas')
    },

    createExtractSchema(schema) {
      return postJson<ExtractionSchema>('/v1/extract/schemas', schema)
    },

    updateExtractSchema(schemaId, schema) {
      // 마스터 아니면 서버가 403, 없으면 404.
      return putJson<ExtractionSchema>(`/v1/extract/schemas/${schemaId}`, schema)
    },

    async deleteExtractSchema(schemaId) {
      // 204 No Content. 마스터 아니면 서버가 403.
      await ensureOk(
        await fetch(apiUrl(`/v1/extract/schemas/${schemaId}`), {
          method: 'DELETE',
          headers: authHeaders(),
        }),
      )
    },

    async autoExtractSchema(file, options = {}) {
      const form = new FormData()
      form.append('file', file, file.name)
      if (options.docType) form.append('doc_type', options.docType)
      if (options.name) form.append('name', options.name)
      if (options.vlm) form.append('vlm', 'true')
      const response = await ensureOk(
        await fetch(apiUrl('/v1/extract/schemas/auto'), {
          method: 'POST',
          headers: authHeaders(),
          body: form,
          signal: options.signal,
        }),
      )
      return (await response.json()) as ExtractionSchema
    },

    async runExtraction(file, schemaId, options = {}) {
      const form = new FormData()
      form.append('file', file, file.name)
      form.append('schema_id', String(schemaId))
      if (options.vlm) form.append('vlm', 'true')
      const response = await ensureOk(
        await fetch(apiUrl('/v1/extract/run'), {
          method: 'POST',
          headers: authHeaders(),
          body: form,
          signal: options.signal,
        }),
      )
      return (await response.json()) as ExtractResponse
    },

    listExtractResults(docId) {
      return getJson<ExtractionResult[]>(`/v1/extract/results/${encodeURIComponent(docId)}`)
    },
    exportCorrectedExtraction(result) {
      return postJson<ExtractExportResult>('/v1/extract/results/export', result)
    },
  }
}

/**
 * RFC 5987 `filename*=UTF-8''<percent-encoded>` 또는 기본 `filename="…"`에서 이름 추출.
 * 둘 다 없으면 null. 다운로드 파일명 결정에 쓴다.
 */
function filenameFromHeader(value: string | null): string | null {
  if (!value) {
    return null
  }
  const star = /filename\*\s*=\s*[^']*''([^;]+)/i.exec(value)
  if (star) {
    try {
      return decodeURIComponent(star[1].trim())
    } catch {
      // percent-decoding 실패 시 다음 폴백.
    }
  }
  const plain = /filename\s*=\s*"?([^";]+)"?/i.exec(value)
  return plain ? plain[1].trim() : null
}

/** RAG 요청 바디 — `top_k`는 지정됐을 때만 실어 백엔드 기본값을 존중한다. */
function ragBody(query: string, options: RagOptions): { query: string; top_k?: number; disabled_sources?: SourceType[] } {
  const body: { query: string; top_k?: number; disabled_sources?: SourceType[] } = { query }
  if (options.topK !== undefined) body.top_k = options.topK
  if (options.disabledSources && options.disabledSources.length > 0) {
    body.disabled_sources = options.disabledSources
  }
  return body
}


/** 2xx면 그대로, 아니면 상태코드 + 본문 detail로 throw(토큰은 본문에 없어 안전). */
async function ensureOk(response: Response): Promise<Response> {
  if (response.ok) {
    return response
  }
  const detail = await safeErrorDetail(response)
  throw new ApiError(
    response.status,
    `요청 실패: ${response.status} ${response.statusText}${detail ? ` — ${detail}` : ''}`,
  )
}

/** 에러 본문에서 사람이 읽을 detail을 뽑는다(FastAPI는 `{detail}`). 실패는 무시. */
async function safeErrorDetail(response: Response): Promise<string> {
  let text: string
  try {
    text = await response.text()
  } catch {
    // 본문 읽기 실패는 무시한다 — 상태코드만으로 충분히 의미 있는 에러다.
    return ''
  }
  if (!text) {
    return ''
  }
  try {
    const data = JSON.parse(text) as { detail?: unknown }
    if (typeof data.detail === 'string') {
      return data.detail
    }
  } catch {
    // JSON이 아니면 원문 텍스트를 그대로 detail로 쓴다(의도된 폴백).
  }
  return text
}

/**
 * `text/event-stream` 본문을 읽어 `data:` 조각을 순서대로 `onChunk`로 넘긴다.
 *
 * 이벤트 경계는 빈 줄(`\n\n`)이며, 한 이벤트의 여러 `data:` 라인은 개행으로 다시
 * 합친다(백엔드가 조각 내 개행을 다중 data 라인으로 보냄). `[DONE]` 센티넬에서
 * 멈춘다. 부분 수신은 버퍼에 모아 경계가 완성될 때만 처리한다.
 */
async function readSse(
  response: Response,
  onChunk: (chunk: string) => void,
): Promise<void> {
  const body = response.body
  if (!body) {
    throw new ApiError(response.status, 'SSE 응답에 본문이 없습니다')
  }
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) {
      break
    }
    buffer += decoder.decode(value, { stream: true })
    let boundary = buffer.indexOf('\n\n')
    while (boundary !== -1) {
      const event = buffer.slice(0, boundary)
      buffer = buffer.slice(boundary + 2)
      const chunk = parseSseEvent(event)
      if (chunk === SSE_DONE) {
        return
      }
      if (chunk !== null) {
        onChunk(chunk)
      }
      boundary = buffer.indexOf('\n\n')
    }
  }
  // 스트림이 빈 줄 없이 끝난 경우, 버퍼에 남은 마지막 이벤트를 마저 처리한다.
  const tail = parseSseEvent(buffer)
  if (tail !== null && tail !== SSE_DONE) {
    onChunk(tail)
  }
}

/** SSE 이벤트 블록에서 `data:` 라인만 모아 개행으로 합친다. data 없으면 null. */
function parseSseEvent(event: string): string | null {
  const dataLines: string[] = []
  for (const line of event.split('\n')) {
    if (line.startsWith('data:')) {
      // SSE 규약: 콜론 뒤 선행 공백 하나를 제거한다.
      dataLines.push(line.startsWith('data: ') ? line.slice(6) : line.slice(5))
    }
  }
  return dataLines.length === 0 ? null : dataLines.join('\n')
}
