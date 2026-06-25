/**
 * 최근 본 문서 — 클라이언트 로컬 기록(localStorage).
 *
 * 백엔드에 "최근 문서" 엔드포인트가 없어, 사용자가 검색 결과에서 연 문서를 브라우저에
 * **로컬로만** 쌓는다(서버 미전송). 권한·내용은 서버 권위이며 여기엔 표시용 메타만 둔다.
 * 파싱 실패(손상/스키마 변경)는 앱을 죽이지 않게 빈 목록으로 복구한다 — 최근 목록은
 * 보조 UX라 복구 불가 값은 다음 기록이 덮어쓰게 둔다.
 */

export interface RecentDoc {
  id: string
  title: string
  type: string
  source: string
  date: string
}

const KEY = 'docux:recent-docs'
const LIMIT = 8

export function getRecentDocs(): RecentDoc[] {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) {
      return []
    }
    const parsed: unknown = JSON.parse(raw)
    return Array.isArray(parsed) ? (parsed as RecentDoc[]) : []
  } catch {
    // 손상된 값 — 복구 불가하므로 빈 목록으로 시작한다(다음 기록이 덮어쓴다).
    return []
  }
}

export function addRecentDoc(doc: RecentDoc): void {
  try {
    const next = [doc, ...getRecentDocs().filter((d) => d.id !== doc.id)].slice(
      0,
      LIMIT,
    )
    localStorage.setItem(KEY, JSON.stringify(next))
  } catch {
    // localStorage 불가(프라이빗 모드 등) — 최근 목록은 보조 기능이라 무시한다.
  }
}
