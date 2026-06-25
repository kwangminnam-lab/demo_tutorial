"""파일 단위 어휘(키워드) 검색 인덱스 인터페이스.

검색 화면의 파일 단위 통합 검색 경계다. 의미 유사도(vectorstore/OpenSearch k-NN,
RAG용 청크 임베딩)와 **별개**로 둔다 — 어휘 검색은 제목·요약·태그·작성자에
대한 키워드/오타 근접 매칭을, 의미 검색은 청크 임베딩 유사도를 담당한다.

권한 인지(ADR-005)는 `search`의 후보 선별 단계에서 강제한다 — 사용자 권한 밖
파일이 결과로 새지 않도록 점수 계산 전에 거른다(사후 필터 의존 금지).
부서 가중(ADR-013)은 권한 통과분의 점수만 올리는 soft boost다 — 노출 여부를
바꾸지 않는다(권한과 혼동 금지).

구현은 두 개: `InMemorySearchIndex`(테스트·dev 기본, 서버 불요),
`OpenSearchIndex`(실구현, 후속 step). 둘은 같은 인터페이스를 만족한다.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from kms.domain.access import AccessLevel
from kms.domain.models import FileDoc, FileHit, SourceType


@runtime_checkable
class SearchIndex(Protocol):
    """파일 단위 어휘 검색 인덱스 계약 (OpenSearch k-NN 의미검색과 분리)."""

    def index_file(self, doc: FileDoc) -> None:
        """파일 도큐먼트를 색인한다. 같은 `doc_id`는 덮어쓴다(멱등 upsert)."""
        ...

    def get(self, doc_id: str) -> FileDoc | None:
        """`doc_id`로 색인된 파일 메타를 반환. 없으면 None.

        파일 원본 접근(`GET /v1/files/{doc_id}`)의 메타 해소 진입점이다 —
        권한 체크는 호출자(서비스/라우트)가 한다(여기선 단순 조회).
        """
        ...

    def get_by_source_url(self, source_url: str) -> FileDoc | None:
        """`source_url`로 색인된 파일을 역조회. 없으면 None.

        RAG citation에서 chunk 메타의 source_url로 file을 찾아 다운로드 가능한
        file doc_id를 채우기 위함. 권한 체크는 호출자 책임 (단순 조회).
        """
        ...

    def delete(self, doc_id: str) -> bool:
        """`doc_id` 파일 메타를 삭제한다. 존재해서 지웠으면 True, 없었으면 False.

        권한 체크는 호출자(라우트/서비스) 책임 — 여기선 단순 삭제다.
        """
        ...

    def count_by_source(
        self,
        access_level: AccessLevel,
        *,
        text: str | None = None,
    ) -> tuple[int, dict[str, int]]:
        """소스별 문서 수 + 합계를 반환한다(권한 인지).

        `text`가 None이면 권한 내 전체 코퍼스 통계(대시보드 커넥터 카운트).
        `text`가 있으면 해당 검색어에 매칭되는 문서들의 소스별 카운트(검색 facets).
        반환: `(total, {source_value: count})` — source는 도메인 SourceType 값.
        """
        ...

    def search(
        self,
        text: str,
        access_level: AccessLevel,
        *,
        source_filter: SourceType | None = None,
        top_k: int = 10,
        department: str | None = None,
        department_boost_weight: float = 0.0,
        doc_type_filter: str | None = None,
        days_window: int | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[FileHit]:
        """권한 필터(하드) + 부서 가중(soft)을 적용한 파일 단위 검색 결과를 반환한다.

        **권한 인지**: `access_level`로 접근 가능한(`access <= access_level`) 파일만
        반환한다 — 권한 밖 파일은 **검색 단계에서** 제외한다(사후 필터 의존 금지).
        부서 가중은 `department`가 파일 `author_department`와 같을 때
        `department_boost_weight`만큼 점수에 더한다(순위만 바꿈, 노출 여부 불변).
        결과는 점수 내림차순, 최대 `top_k`건.
        """
        ...
