/**
 * RAG 챗봇 페이지.
 *
 * 질의 → `client.ragStream`으로 답변 토큰을 실시간 누적 표시(스트리밍) → 스트림
 * 종료 후 `client.ragAnswer`로 출처 인용(citations)·근거 여부(grounded)를 받아 표시.
 *
 * 왜 두 번 호출하나: 백엔드 SSE 스트림(`rag.py` `_sse_events`)은 **텍스트 조각만**
 * 흘려보내고, citations·grounded는 비스트리밍 Answer(`stream=false`)에만 담긴다.
 * 그래서 답변 본문은 스트리밍으로 즉시 보여주고, 출처는 완료 후 한 번 더 받아 채운다.
 *
 * 클라이언트는 답을 **지어내지 않는다** — 서버 RAG 결과(스트림 텍스트·서버 citations)
 * 만 표시한다. 권한 필터·부서 가중·근거 판단은 모두 서버 권위다(프론트 재구현 금지).
 *
 * 화면은 두 상태로 나뉜다: 빈 대화는 design/chatbot_init.html(중앙 환영 + 제안 카드),
 * 대화 시작 후엔 design/chatbot_result.html(좌측 어시스턴트·우측 사용자 말풍선). 하단
 * 입력 바는 공통. `client`는 주입받는다(DI): 테스트는 모킹 클라이언트를 넘긴다.
 */

import {
  Children,
  useEffect,
  useState,
  useSyncExternalStore,
  type FormEvent,
  type ReactNode,
} from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeRaw from 'rehype-raw'
import type { ApiClient, LlmProvider } from '../api/client'
import type { Citation } from '../api/types'
import { downloadDoc } from '../lib/fileActions'
import { CircularProgress, useTimedProgress } from '../app/CircularProgress'
import { IconChat, IconSend } from '../app/icons'
import {
  deleteSession,
  listSessions,
  type ChatSession,
} from '../lib/chatSessions'
import { chatStore, type ChatTurn } from '../lib/chatStore'
import { updateSettings } from '../lib/userSettings'

// 챗봇 모델 선택지 — qwen3(사내 vLLM)는 미배선이라 제외, gpt-oss만 노출.
// (백엔드 router는 qwen3 provider를 여전히 지원하나 UI에선 제공하지 않는다.)
const PROVIDER_LABELS: Partial<Record<LlmProvider, string>> = {
  gemma: 'gpt-oss-120b',
}

export function ChatPage({ client }: { client: ApiClient }) {
  // 모든 챗 상태는 모듈 싱글톤 store에 있다 — 탭 이동 → unmount → remount 시 그대로.
  // 진행 중인 ragStream도 컴포넌트 lifecycle과 무관하게 계속 흐른다.
  const storeState = useSyncExternalStore(chatStore.subscribe, chatStore.getSnapshot)
  const { input, turns, sessionId, provider, isStreaming } = storeState
  const [sessions, setSessions] = useState<ChatSession[]>([])
  // 대화 기록·새 채팅 드로어 — 기본 닫힘. 아이콘 클릭 시 옆으로 슬라이드 열림/닫힘.
  const [sidebarOpen, setSidebarOpen] = useState(false)

  // 마운트 시 세션 목록 로드.
  useEffect(() => {
    setSessions(listSessions())
  }, [])

  // 세션 목록은 store의 turns 변경에 따라 다시 읽는다 (store가 영속까지 처리).
  useEffect(() => {
    setSessions(listSessions())
  }, [turns])

  const setInput = (v: string) => chatStore.setInput(v)

  function handleNewChat() {
    chatStore.newChat()
    setSessions(listSessions())
  }

  function handleSelectSession(s: ChatSession) {
    chatStore.selectSession(s)
  }

  function handleDeleteSession(id: string, e: React.MouseEvent) {
    e.stopPropagation()
    deleteSession(id)
    setSessions(listSessions())
    if (sessionId === id) handleNewChat()
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!input.trim() || isStreaming) return
    void chatStore.send(client, input)
  }

  function handleStop() {
    chatStore.stop()
  }

  function setProvider(next: LlmProvider) {
    chatStore.setProvider(next)
    updateSettings({ selectedProvider: next })
  }

  return (
    <section className="flex h-full bg-white">
      {/* 좌측 세션 드로어 — 아이콘 토글로 열고/닫는다. 닫히면 폭 0으로 슬라이드. */}
      <aside
        aria-hidden={!sidebarOpen}
        className={`flex h-full shrink-0 flex-col overflow-hidden bg-[#f8f9fa] transition-[width] duration-200 ${
          sidebarOpen ? 'w-64 border-r border-[#e5e7eb]' : 'w-0'
        }`}
      >
        <div className="flex h-full w-64 flex-col">
        <div className="p-3">
          <button
            type="button"
            onClick={handleNewChat}
            aria-label="새 채팅"
            className="flex w-full cursor-pointer items-center justify-center gap-2 rounded-lg border border-[#d1d5db] bg-white px-3 py-2 text-sm font-semibold text-[#374151] hover:bg-[#eef2ff] hover:text-[#1d4ed8]"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <line x1="12" y1="5" x2="12" y2="19" />
              <line x1="5" y1="12" x2="19" y2="12" />
            </svg>
            새 채팅
          </button>
        </div>
        <div className="px-3 pb-2 text-[11px] font-bold uppercase tracking-wider text-[#9ca3af]">
          대화 기록
        </div>
        <nav aria-label="대화 세션" className="flex flex-1 flex-col gap-1 overflow-y-auto px-2 pb-3">
          {sessions.length === 0 ? (
            <p className="px-2 py-4 text-center text-xs text-[#9ca3af]">아직 대화가 없습니다.</p>
          ) : (
            sessions.map((s) => {
              const active = s.id === sessionId
              return (
                <div
                  key={s.id}
                  onClick={() => handleSelectSession(s)}
                  className={`group flex cursor-pointer items-start justify-between gap-1 rounded-lg px-3 py-2 text-sm transition-colors ${
                    active
                      ? 'bg-[#eef2ff] text-[#1d4ed8] font-semibold'
                      : 'text-[#374151] hover:bg-[#f3f4f6]'
                  }`}
                >
                  <span className="line-clamp-2 min-w-0 flex-1 leading-snug" title={s.title}>
                    {s.title}
                  </span>
                  <button
                    type="button"
                    onClick={(e) => handleDeleteSession(s.id, e)}
                    aria-label="세션 삭제"
                    className="opacity-0 transition-opacity group-hover:opacity-100 hover:text-[#dc2626]"
                  >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
                      <line x1="6" y1="6" x2="18" y2="18" />
                      <line x1="6" y1="18" x2="18" y2="6" />
                    </svg>
                  </button>
                </div>
              )
            })
          )}
        </nav>
        </div>
      </aside>

      <div className="flex h-full min-w-0 flex-1 flex-col bg-white">
        {/* 상단 바 — 드로어 토글 아이콘(열림/닫힘). 닫혀 있으면 이 아이콘만 보인다. */}
        <div className="flex items-center gap-2 border-b border-[#e5e7eb] px-3 py-2">
          <button
            type="button"
            onClick={() => setSidebarOpen((open) => !open)}
            aria-label={sidebarOpen ? '대화 기록 닫기' : '대화 기록 열기'}
            aria-expanded={sidebarOpen}
            className="flex h-9 w-9 cursor-pointer items-center justify-center rounded-md text-[#6b7280] transition-colors hover:bg-[#f3f4f6] hover:text-[#111827]"
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <rect x="3" y="4" width="18" height="16" rx="2" />
              <line x1="9" y1="4" x2="9" y2="20" />
            </svg>
          </button>
          <span className="text-sm font-semibold text-[#374151]">챗봇</span>
        </div>
      {turns.length === 0 ? (
        <WelcomeHero />
      ) : (
        <ul
          aria-label="대화"
          className="mx-auto flex w-full max-w-4xl flex-1 list-none flex-col gap-8 overflow-y-auto p-0 px-8 py-8"
        >
          {turns.map((turn) => (
          <li key={turn.id} className="flex flex-col gap-6">
            {/* 사용자 질의 — 우측 정렬 */}
            <div className="flex justify-end">
              <p className="m-0 max-w-2xl rounded-xl rounded-tr-none border border-[#e5e7eb] bg-[#f8f9fa] px-5 py-4 text-sm leading-relaxed text-[#1f2937]">
                {turn.query}
              </p>
            </div>

            {/* 어시스턴트 답변 — 좌측 정렬 */}
            <div className="flex items-start gap-4">
              <AssistantAvatar />
              <div className="min-w-0 max-w-2xl rounded-xl rounded-tl-none border border-[#e5e7eb] bg-white px-5 py-4 shadow-sm">
                <div className="m-0 text-sm leading-relaxed text-[#1f2937]">
                  <AnswerWithCitations
                    text={turn.answer}
                    citations={turn.citations}
                    client={client}
                  />
                </div>
                {turn.status === 'streaming' ? (
                  <ChatStreamingIndicator hasAnswer={turn.answer.length > 0} />
                ) : null}
                {turn.status === 'error' ? (
                  <p role="alert" className="mt-1 text-sm font-medium text-[#dc2626]">
                    {turn.errorMessage}
                  </p>
                ) : null}
                <TurnCitations turn={turn} />
              </div>
            </div>
          </li>
          ))}
        </ul>
      )}

      <div className="shrink-0 border-t border-[#e5e7eb] bg-white px-8 py-5">
        {/* 모델 선택은 선택지가 2개 이상일 때만 노출(현재 gpt-oss 단독이라 숨김). */}
        {Object.keys(PROVIDER_LABELS).length > 1 && (
          <div className="mx-auto mb-2 flex w-full max-w-4xl items-center justify-end gap-2 text-xs text-[#6b7280]">
            <label htmlFor="provider-select">모델</label>
            <select
              id="provider-select"
              aria-label="LLM 모델 선택"
              value={provider}
              onChange={(event) => setProvider(event.target.value as LlmProvider)}
              className="rounded-md border border-[#d1d5db] bg-white px-2 py-1 text-sm text-[#111827] focus:border-[#1d4ed8] focus:outline-none focus:ring-1 focus:ring-[#1d4ed8]"
            >
              {(Object.keys(PROVIDER_LABELS) as LlmProvider[]).map((p) => (
                <option key={p} value={p}>
                  {PROVIDER_LABELS[p]}
                </option>
              ))}
            </select>
          </div>
        )}
        <form
          onSubmit={handleSubmit}
          aria-label="챗봇"
          className="mx-auto flex w-full max-w-4xl items-center gap-2 rounded-lg border border-[#d1d5db] bg-white p-2 pl-4 shadow-sm focus-within:border-[#1d4ed8] focus-within:ring-1 focus-within:ring-[#1d4ed8]"
        >
          <input
            type="text"
            name="query"
            aria-label="질문"
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder="질문을 입력하세요"
            className="min-w-0 flex-1 border-0 bg-transparent text-sm text-[#111827] outline-none placeholder:text-[#9ca3af]"
          />
          {isStreaming ? (
            <button
              type="button"
              onClick={handleStop}
              className="cursor-pointer rounded-lg border border-[#d1d5db] px-3 py-2 text-sm font-semibold text-[#374151] hover:bg-[#f3f4f6]"
            >
              중단
            </button>
          ) : null}
          <button
            type="submit"
            disabled={isStreaming || input.trim() === ''}
            className="flex cursor-pointer items-center gap-1.5 rounded-lg bg-[#1d4ed8] px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-[#1e40af] disabled:cursor-not-allowed disabled:opacity-40"
          >
            <IconSend />
            전송
          </button>
        </form>
      </div>
      </div>
    </section>
  )
}

/**
 * 빈 대화 환영 화면 — design/chatbot_init.html.
 *
 * 중앙 정렬 아이콘 + 헤딩 + 안내.
 */
/**
 * 답변 스트리밍 인디케이터.
 *  - hasAnswer=false (첫 chunk 전): 0→90% 채우는 progress + "검색·생각 중"
 *  - hasAnswer=true (스트리밍 중): 인디터미네이트 회전 + "응답 중"
 */
function ChatStreamingIndicator({ hasAnswer }: { hasAnswer: boolean }) {
  // 검색+첫 토큰까지 ~5초 예상.
  const prog = useTimedProgress(!hasAnswer, 5000)
  return (
    <div aria-live="polite" className="mt-2 flex items-center gap-2">
      <CircularProgress
        value={hasAnswer ? undefined : prog}
        size={24}
        thickness={3}
        color="#1d4ed8"
        ariaLabel={hasAnswer ? '응답 중' : '검색·생각 중'}
      />
      <span className="text-xs text-[#6b7280]">
        {hasAnswer ? '응답 중…' : `검색·생각 중 ${Math.floor(prog)}%`}
      </span>
    </div>
  )
}

function WelcomeHero() {
  return (
    <div className="flex flex-1 flex-col items-center justify-center overflow-y-auto px-8 py-12">
      <div className="mb-6 flex h-16 w-16 items-center justify-center rounded-2xl bg-[#eef2ff] text-[#1d4ed8]">
        <IconChat />
      </div>
      <h1 className="mb-4 text-center text-3xl font-bold tracking-tight text-[#111827]">
        무엇을 도와드릴까요?
      </h1>
      <p className="max-w-2xl text-center text-[15px] leading-relaxed text-[#6b7280]">
        DocuX가 사내 자료 근거로 질문에 답하고, 답변의 출처를 함께 보여드립니다.
      </p>
    </div>
  )
}

/** 어시스턴트 아바타 — 파랑 사각 + 로봇 아이콘(design/chatbot). */
function AssistantAvatar() {
  return (
    <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-[#1d4ed8] text-white shadow-sm">
      <IconChat />
    </div>
  )
}

/**
 * 완료된 턴의 상태 표시 — grounded=false면 "근거 없음"만 알린다.
 *
 * 출처(citations) 목록은 별도 패널로 노출하지 않는다. 대신 답변 본문의 [n] 마커가
 * `AnswerWithCitations`에서 원형 배지로 변환되어 hover 툴팁·클릭 다운로드를 제공.
 * 근거 없음 경고는 답변이 근거에 기반하지 않았음을 알리는 상태이므로 명시한다
 * (근거 없는 답을 조용히 보여주지 않음).
 */
function TurnCitations({ turn }: { turn: ChatTurn }) {
  if (turn.status !== 'done' || turn.grounded === null) {
    return null
  }
  if (turn.grounded === false) {
    return (
      <p
        role="note"
        className="mt-2 rounded border border-[#FEF3C7] bg-[#FEF9E7] px-3 py-2 text-xs text-[#8a6d00]"
      >
        근거 없음 — 검색된 근거가 없어 답변의 근거를 보장할 수 없습니다.
      </p>
    )
  }
  return null
}

/**
 * 답변 텍스트 안 인용 마커를 클릭 가능한 원형 배지로 렌더.
 *
 * 번호 재매김: LLM이 [3], [1], [3] 식으로 검색 결과 원래 인덱스를 쓰더라도
 * **본문 등장 순서대로 1부터 다시 매긴다**. 같은 원래 번호는 같은 새 번호 유지
 * (재인용은 같은 배지). 사용자는 [1]→[2]→... 자연스러운 흐름으로 본다.
 *
 * 전처리: LLM "(근거 …)" 괄호군 통째 제거 (본문 가독성).
 */
// 인용 마커 패턴 — `[3]`, `[3, 4, 5]` 등.
const _CITATION_RE = /\[(\d+(?:\s*,\s*\d+)*)\]/g

function AnswerWithCitations({
  text,
  citations,
  client,
}: {
  text: string
  citations: Citation[]
  client: ApiClient
}) {
  const cleaned = text
    .replace(/[(（]\s*근거[^)）]*[)）]/g, '')
    .replace(/[ \t]{2,}/g, ' ')
    .replace(/ +\./g, '.')
    .trimEnd()

  // 본문 등장 순서대로 새 번호 발급. originalIndex → displayNumber.
  const remap = new Map<number, number>()
  function getDisplayNumber(original: number): number {
    const existing = remap.get(original)
    if (existing !== undefined) return existing
    const next = remap.size + 1
    remap.set(original, next)
    return next
  }

  // 마크다운 렌더가 끝난 **텍스트 노드** 안의 `[n]`만 배지로 치환한다. 텍스트를 미리
  // 쪼개지 않으므로 굵게·목록·표 안에 인용이 들어가도 마크다운이 깨지지 않는다.
  function injectCitations(children: ReactNode): ReactNode {
    return Children.toArray(children).flatMap((child, ci) => {
      if (typeof child !== 'string') return [child]
      const out: ReactNode[] = []
      let last = 0
      let m: RegExpExecArray | null
      _CITATION_RE.lastIndex = 0
      while ((m = _CITATION_RE.exec(child)) !== null) {
        if (m.index > last) out.push(child.slice(last, m.index))
        const originals = m[1]
          .split(',')
          .map((s) => Number.parseInt(s.trim(), 10))
          .filter((n) => Number.isFinite(n))
        out.push(
          <span
            key={`ci-${ci}-${m.index}`}
            className="inline-flex gap-0.5 align-baseline"
          >
            {originals.map((original) => (
              <CitationBadge
                key={original}
                displayNumber={getDisplayNumber(original)}
                citation={citations[original - 1]}
                client={client}
              />
            ))}
          </span>,
        )
        last = _CITATION_RE.lastIndex
      }
      if (last < child.length) out.push(child.slice(last))
      return out.length ? out : [child]
    })
  }

  // 마크다운 컴포넌트 — 텍스트를 담는 요소는 children에 인용 주입을 적용한다.
  // 표 헤더(thead)는 회색 배경으로 본문과 구분한다.
  const components: Components = {
    p: ({ children }) => (
      <p className="my-1 first:mt-0 last:mb-0">{injectCitations(children)}</p>
    ),
    ul: ({ children }) => <ul className="my-1 list-disc pl-5">{children}</ul>,
    ol: ({ children }) => <ol className="my-1 list-decimal pl-5">{children}</ol>,
    li: ({ children }) => <li className="my-0.5">{injectCitations(children)}</li>,
    h1: ({ children }) => (
      <h3 className="mb-1 mt-2 text-base font-bold">{injectCitations(children)}</h3>
    ),
    h2: ({ children }) => (
      <h3 className="mb-1 mt-2 text-base font-bold">{injectCitations(children)}</h3>
    ),
    h3: ({ children }) => (
      <h4 className="mb-1 mt-2 text-sm font-bold">{injectCitations(children)}</h4>
    ),
    strong: ({ children }) => (
      <strong className="font-semibold">{injectCitations(children)}</strong>
    ),
    em: ({ children }) => <em>{injectCitations(children)}</em>,
    code: ({ children }) => (
      <code className="rounded bg-[#f3f4f6] px-1 py-0.5 text-[12px]">{children}</code>
    ),
    a: ({ href, children }) => (
      <a href={href} target="_blank" rel="noreferrer" className="text-[#1d4ed8] underline">
        {injectCitations(children)}
      </a>
    ),
    table: ({ children }) => (
      <div className="my-2 overflow-x-auto">
        <table className="border-collapse text-[13px]">{children}</table>
      </div>
    ),
    thead: ({ children }) => <thead className="bg-[#f3f4f6]">{children}</thead>,
    th: ({ children }) => (
      <th className="border border-[#d1d5db] px-2 py-1 text-left font-semibold text-[#374151]">
        {injectCitations(children)}
      </th>
    ),
    td: ({ children }) => (
      <td className="border border-[#e5e7eb] px-2 py-1 align-top">
        {injectCitations(children)}
      </td>
    ),
  }

  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[rehypeRaw]}
      components={components}
    >
      {cleaned}
    </ReactMarkdown>
  )
}

/**
 * 원형 인용 배지 — 동그라미 안 번호. hover 시 문서 제목·페이지 툴팁, 클릭 시 다운로드.
 *
 * doc_id는 사용자에게 노출하지 않음 (내부 식별자). 툴팁은 title + 페이지/슬라이드.
 * 다운로드 URL 우선순위:
 *   1) /v1/files/{doc_id}?download=true (서버 라우트, file의 doc_id가 채워진 경우)
 *   2) citation.source_url (외부 원본 — 폴백)
 * citation 없으면(LLM 잘못된 번호) 회색 비활성.
 */
function CitationBadge({
  displayNumber,
  citation,
  client,
}: {
  displayNumber: number
  citation: Citation | undefined
  client: ApiClient
}) {
  if (!citation) {
    return (
      <span
        title="인용된 문서를 찾을 수 없습니다"
        className="ml-0.5 inline-flex h-4 min-w-4 cursor-not-allowed items-center justify-center rounded-full bg-[#e5e7eb] px-1 text-[10px] font-bold text-[#9ca3af]"
      >
        {displayNumber}
      </span>
    )
  }
  const titleLabel =
    citation.title ?? citation.snippet.slice(0, 60).trim() ?? '문서'
  const locationParts: string[] = []
  if (citation.page != null) locationParts.push(`p.${citation.page}`)
  if (citation.slide_no != null) locationParts.push(`슬라이드 ${citation.slide_no}`)
  const location = locationParts.length > 0 ? ` (${locationParts.join(', ')})` : ''
  const tooltip = `${titleLabel}${location}\n클릭하여 원본 다운로드`
  // fetch+blob 다운로드 — 새 탭(<a target="_blank">)은 Authorization 헤더가 없어
  // 401됨. ApiClient가 토큰을 헤더로 실어 fetch → blob → 원본 파일명으로 저장.
  return (
    <button
      type="button"
      title={tooltip}
      aria-label={`출처 ${displayNumber}: ${titleLabel}`}
      onClick={() => void downloadDoc(client, citation.doc_id)}
      className="ml-0.5 inline-flex h-4 min-w-4 cursor-pointer items-center justify-center rounded-full border-0 bg-[#1d4ed8] px-1 text-[10px] font-bold text-white hover:bg-[#1e40af]"
    >
      {displayNumber}
    </button>
  )
}

