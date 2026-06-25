"""통합 검색 라우트 (GET /v1/search) — 권한 인지 검색의 HTTP 경계.

진입점은 얇게(CONVENTIONS·ARCHITECTURE): 쿼리 파싱 → 인증된 `UserContext` 획득
→ `SearchQuery` 구성 → `SearchService.search` 호출 → 결과를 JSON으로 매핑만 한다.
권한 필터·부서 가중 등 비즈니스 로직은 전부 `SearchService`에 있다.

권한 강제: `get_current_user`로 사용자를 얻고 그 `UserContext`를 그대로 서비스에
넘긴다. 권한 밖 문서는 서비스가 retrieval 단계에서 거르므로 응답에 새지 않는다
(API 테스트로 재확인).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from kms.api.deps import get_current_user, get_search_service
from kms.domain.models import (
    DocumentMetadata,
    FileHit,
    SearchQuery,
    SourceType,
    UserContext,
)
from kms.services.search_service import SearchService

router = APIRouter(prefix="/v1", tags=["search"])


class SearchHit(BaseModel):
    """검색 결과 1건(파일 단위)의 응답 표현 — 제목·발췌·점수·출처 메타.

    `text`는 파일 요약/발췌(`FileDoc.description`), `metadata`에 `source`(출처)·
    작성자·부서·원본 URL·적재일자가 담긴다. 권한 밖 문서는 서비스가 이미 걸렀으므로
    여기에는 사용자 권한 내 결과만 들어온다.

    `title`·`tags`·`doc_type`은 파일 단위 검색용 추가 필드다(기존 필드는 유지 —
    비파괴 확장, ADR-004).
    """

    doc_id: str
    text: str
    score: float
    metadata: DocumentMetadata
    title: str
    tags: list[str] = []
    doc_type: str | None = None


@router.get("/search", response_model=list[SearchHit])
def search(
    q: str = Query(min_length=1, description="검색어"),
    user: UserContext = Depends(get_current_user),
    service: SearchService = Depends(get_search_service),
    source: SourceType | None = Query(default=None, description="소스 종류 필터"),
    top_k: int = Query(default=10, ge=1, le=100, description="반환 최대 개수"),
    doc_type: str | None = Query(default=None, description="문서 유형 필터(PDF/DOCX/...)"),
    days: int | None = Query(default=None, ge=1, le=3650, description="최근 N일 필터"),
    date_from: str | None = Query(default=None, description="시작일 YYYY-MM-DD"),
    date_to: str | None = Query(default=None, description="종료일 YYYY-MM-DD"),
) -> list[SearchHit]:
    """권한 인지 통합 검색. 사용자 권한 내 문서만, 같은 부서 문서를 상위로 반환한다.

    결과는 **파일 단위**다 — 어휘 인덱스(`SearchService.search_files`)에서 파일당
    1건으로 검색한다. 청크 단위 근거가 필요한 RAG는 청크 의미검색을 직접 쓴다
    (이 라우트와 분리).
    """
    query = SearchQuery(
        text=q,
        source_filter=source,
        top_k=top_k,
        doc_type_filter=doc_type,
        days_window=days,
        date_from=date_from,
        date_to=date_to,
    )
    results = service.search_files(query, user)
    return [_to_hit(result) for result in results]


class SourceFacets(BaseModel):
    """소스별 카운트 facets — 대시보드 커넥터 통계 + 검색 결과 분포 공유."""

    total: int
    by_source: dict[str, int]


@router.get("/search/facets", response_model=SourceFacets)
def search_facets(
    q: str | None = Query(default=None, description="검색어(없으면 권한 내 전체 코퍼스 통계)"),
    user: UserContext = Depends(get_current_user),
    service: SearchService = Depends(get_search_service),
) -> SourceFacets:
    """소스별 문서 카운트(권한 인지). `q` 미지정 시 코퍼스 전체, 지정 시 매칭 모집단."""
    total, by_source = service.facets_by_source(user, text=q)
    return SourceFacets(total=total, by_source=by_source)


def _to_hit(hit: FileHit) -> SearchHit:
    """도메인 `FileHit` → 응답 `SearchHit` 매핑 (얇은 변환).

    `metadata`는 `FileDoc`의 출처 필드로 채운다 — 권한 필터는 서비스가 이미 적용.
    """
    file = hit.file
    return SearchHit(
        doc_id=file.doc_id,
        text=file.description,
        score=hit.score,
        metadata=DocumentMetadata(
            source=file.source,
            access=file.access,
            author=file.author,
            author_department=file.author_department,
            source_url=file.source_url,
            ingested_at=file.ingested_at,
        ),
        title=file.title,
        tags=file.tags,
        doc_type=file.doc_type,
    )
