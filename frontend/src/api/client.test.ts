import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, createClient } from './client'
import type { Answer, SearchHit } from './types'

// 누출 검출용 토큰 — 어떤 에러 메시지·URL에도 절대 나타나면 안 된다.
const TOKEN = 'super-secret-token-DO-NOT-LEAK'

const fetchMock = vi.fn<typeof fetch>()

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  fetchMock.mockReset()
  vi.unstubAllGlobals()
})

/** 마지막 fetch 호출의 (url, init)를 꺼낸다. */
function lastCall(): { url: string; init: RequestInit } {
  const [url, init] = fetchMock.mock.calls.at(-1) as [string, RequestInit]
  return { url, init }
}

/** JSON 응답을 흉내내는 최소 Response. */
function jsonResponse(
  data: unknown,
  init: { ok?: boolean; status?: number; statusText?: string } = {},
): Response {
  return {
    ok: init.ok ?? true,
    status: init.status ?? 200,
    statusText: init.statusText ?? 'OK',
    json: async () => data,
    text: async () => JSON.stringify(data),
  } as unknown as Response
}

/** 여러 read()로 나눠 전달되는 SSE 스트림 Response를 흉내낸다. */
function streamResponse(parts: string[]): Response {
  const encoder = new TextEncoder()
  let index = 0
  const reader = {
    read: async () => {
      if (index < parts.length) {
        return { done: false, value: encoder.encode(parts[index++]) }
      }
      return { done: true, value: undefined }
    },
  }
  return {
    ok: true,
    status: 200,
    statusText: 'OK',
    body: { getReader: () => reader },
  } as unknown as Response
}

describe('createClient.search', () => {
  it('토큰 헤더를 싣고 쿼리 파라미터를 정확히 구성하며 SearchHit[]를 파싱한다', async () => {
    const hits: SearchHit[] = [
      {
        doc_id: 'd1',
        title: '요금정책.pdf',
        text: '요금 정책 본문',
        score: 0.92,
        metadata: { source: 'onedrive', access: 1, author_department: 'AI플랫폼' },
      },
    ]
    fetchMock.mockResolvedValueOnce(jsonResponse(hits))
    const client = createClient(() => TOKEN)

    const result = await client.search('요금정책', { source: 'onedrive', topK: 5 })

    expect(result).toEqual(hits)
    const { url, init } = lastCall()
    const parsed = new URL(url, 'http://localhost')
    expect(parsed.pathname).toBe('/v1/search')
    expect(parsed.searchParams.get('q')).toBe('요금정책')
    expect(parsed.searchParams.get('source')).toBe('onedrive')
    expect(parsed.searchParams.get('top_k')).toBe('5')
    expect(new Headers(init.headers).get('Authorization')).toBe(`Bearer ${TOKEN}`)
  })

  it('선택 옵션이 없으면 source·top_k 파라미터를 붙이지 않는다', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse([]))
    const client = createClient(() => TOKEN)

    await client.search('q만')

    const parsed = new URL(lastCall().url, 'http://localhost')
    expect(parsed.searchParams.has('source')).toBe(false)
    expect(parsed.searchParams.has('top_k')).toBe(false)
  })

  it('미인증(토큰 null)이면 Authorization 헤더를 생략한다', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse([]))
    const client = createClient(() => null)

    await client.search('q')

    expect(new Headers(lastCall().init.headers).has('Authorization')).toBe(false)
  })
})

describe('createClient.ragAnswer', () => {
  it('stream=false로 POST하고 Answer를 파싱한다', async () => {
    const answer: Answer = {
      text: '요약 결과',
      citations: [{ source: 'slack', doc_id: 'd1', snippet: '근거' }],
      grounded: true,
    }
    fetchMock.mockResolvedValueOnce(jsonResponse(answer))
    const client = createClient(() => TOKEN)

    const result = await client.ragAnswer('Runway 요금정책 요약', { topK: 8 })

    expect(result).toEqual(answer)
    const { url, init } = lastCall()
    expect(new URL(url, 'http://localhost').searchParams.get('stream')).toBe('false')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body as string)).toEqual({
      query: 'Runway 요금정책 요약',
      top_k: 8,
    })
  })
})

describe('createClient.ragStream', () => {
  it('SSE data 조각을 순서대로 onChunk에 전달하고 [DONE]에서 멈춘다', async () => {
    // read() 경계가 이벤트 경계와 어긋나도(조각 분할) 올바르게 재조립해야 한다.
    fetchMock.mockResolvedValueOnce(
      streamResponse([
        'data: 안녕',
        '하세요\n\ndata: 두번째',
        '\n\ndata: [DONE]\n\ndata: 무시됨\n\n',
      ]),
    )
    const client = createClient(() => TOKEN)
    const chunks: string[] = []

    await client.ragStream('질문', (chunk) => chunks.push(chunk))

    expect(chunks).toEqual(['안녕하세요', '두번째'])
    expect(lastCall().init.method).toBe('POST')
  })
})

/** 텍스트(NDJSON) 응답을 흉내내는 최소 Response. */
function textResponse(
  body: string,
  init: { ok?: boolean; status?: number; statusText?: string } = {},
): Response {
  return {
    ok: init.ok ?? true,
    status: init.status ?? 200,
    statusText: init.statusText ?? 'OK',
    text: async () => body,
  } as unknown as Response
}

describe('createClient.parseJsonl', () => {
  it('FormData로 /v1/parse/jsonl에 POST하고 NDJSON 텍스트를 반환한다', async () => {
    const ndjson = '{"section_index":0,"level":1,"title":"제목","page":1}\n'
    fetchMock.mockResolvedValueOnce(textResponse(ndjson))
    const client = createClient(() => TOKEN)
    const file = new File(['x'], 'doc.pdf', { type: 'application/pdf' })

    const text = await client.parseJsonl(file)

    expect(text).toBe(ndjson)
    const { url, init } = lastCall()
    expect(new URL(url, 'http://localhost').pathname).toBe('/v1/parse/jsonl')
    expect(init.method).toBe('POST')
    expect(init.body).toBeInstanceOf(FormData)
    expect(new Headers(init.headers).get('Authorization')).toBe(`Bearer ${TOKEN}`)
  })

  it('비-2xx면 ApiError를 던지고 토큰을 노출하지 않는다', async () => {
    fetchMock.mockResolvedValueOnce(textResponse('', { ok: false, status: 422, statusText: 'Unprocessable' }))
    const client = createClient(() => TOKEN)

    const error = await client
      .parseJsonl(new File(['x'], 'scan.png'))
      .catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(422)
    expect((error as ApiError).message).not.toContain(TOKEN)
  })
})

describe('createClient 에러 처리', () => {
  it('401이면 throw하고 메시지에 토큰을 노출하지 않는다', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ detail: '인증 필요' }, { ok: false, status: 401, statusText: 'Unauthorized' }),
    )
    const client = createClient(() => TOKEN)

    const error = await client.search('q').catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(401)
    expect((error as ApiError).message).toContain('401')
    expect((error as ApiError).message).not.toContain(TOKEN)
  })

  it('500이면 throw하고 메시지에 토큰을 노출하지 않는다', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        { detail: '내부 오류' },
        { ok: false, status: 500, statusText: 'Internal Server Error' },
      ),
    )
    const client = createClient(() => TOKEN)

    const error = await client.ragAnswer('q').catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(500)
    expect((error as ApiError).message).toContain('500')
    expect((error as ApiError).message).not.toContain(TOKEN)
  })
})
