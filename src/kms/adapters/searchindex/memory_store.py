"""dict 기반 인메모리 어휘 검색 인덱스 — 테스트·dev 기본 경로.

OpenSearch 서버 없이 파일 단위 어휘 검색을 돌리기 위한 경량 구현(graph의
`InMemoryGraphStore`와 같은 패턴). 어휘 점수는 표준 라이브러리 `difflib`만으로
계산해 **결정론**을 보장한다(랜덤·시계 의존 없음). 파생 데이터이므로 영속하지
않는다.

레이어 경계: domain 모델 + 표준 라이브러리만 의존한다. vectorstore/graph 등
다른 어댑터나 프레임워크를 import하지 않는다.
"""

from __future__ import annotations

import difflib

from kms.domain.access import AccessLevel
from kms.domain.models import FileDoc, FileHit, SourceType

# 제목 일치는 본문·태그·작성자보다 관련도가 높다 — 가중을 더 준다.
_TITLE_WEIGHT = 2.0
# difflib 근접 점수는 이 비율 이상일 때만 가산한다. 무관한 긴 본문이 미세한
# ratio로 점수를 얻어 "점수 0 = 제외" 규칙을 무력화하는 것을 막는다.
_TYPO_THRESHOLD = 0.6


def _field_score(query: str, field: str) -> float:
    """쿼리와 한 필드의 어휘 점수 — 부분일치(substring) + 오타 근접(ratio).

    대소문자 무시. 부분일치면 +1.0, difflib 근접 비율이 임계 이상이면 그 비율을
    가산한다. 둘 다 결정론적이다.
    """
    q = query.strip().lower()
    f = field.strip().lower()
    if not q or not f:
        return 0.0
    score = 0.0
    if q in f:
        score += 1.0
    ratio = difflib.SequenceMatcher(None, q, f).ratio()
    if ratio >= _TYPO_THRESHOLD:
        score += ratio
    return score


def _lexical_score(query: str, doc: FileDoc) -> float:
    """파일의 제목·요약·태그·작성자에 대한 합산 어휘 점수 (제목 가중)."""
    score = _TITLE_WEIGHT * _field_score(query, doc.title)
    score += _field_score(query, doc.description)
    score += max((_field_score(query, tag) for tag in doc.tags), default=0.0)
    if doc.author is not None:
        score += _field_score(query, doc.author)
    return score


def _department_boost(
    doc: FileDoc, department: str | None, weight: float
) -> float:
    """부서 가중(soft): 작성자 부서 == 요청 부서면 weight, 아니면 0.0.

    domain.department_boost와 같은 규칙이나, FileDoc(메타데이터 아님)을 받으므로
    인라인한다. 권한 통과분의 순위만 조정한다 — 노출 여부와 무관(ADR-013).
    """
    if (
        department is not None
        and doc.author_department is not None
        and doc.author_department == department
    ):
        return weight
    return 0.0


class InMemorySearchIndex:
    """인메모리 파일 단위 어휘 검색 인덱스. `SearchIndex` 프로토콜을 만족한다."""

    def __init__(self) -> None:
        # doc_id → 파일 도큐먼트 (멱등 upsert로 같은 doc_id는 덮어쓴다).
        self._files: dict[str, FileDoc] = {}

    def index_file(self, doc: FileDoc) -> None:
        self._files[doc.doc_id] = doc

    def get(self, doc_id: str) -> FileDoc | None:
        """`doc_id`로 파일 메타를 반환. 없으면 None (인메모리 dict 조회)."""
        return self._files.get(doc_id)

    def delete(self, doc_id: str) -> bool:
        """`doc_id` 파일을 삭제. 있었으면 True, 없었으면 False."""
        return self._files.pop(doc_id, None) is not None

    def get_by_source_url(self, source_url: str) -> FileDoc | None:
        """`source_url` 매칭 첫 파일을 반환. RAG citation의 chunk→file 역조회용."""
        if not source_url:
            return None
        for doc in self._files.values():
            if doc.source_url == source_url:
                return doc
        return None

    def count_by_source(
        self,
        access_level: AccessLevel,
        *,
        text: str | None = None,
    ) -> tuple[int, dict[str, int]]:
        """권한 + 선택 검색어로 필터한 파일 집합의 소스별 카운트.

        검색어는 `_lexical_score>0`을 매칭 기준으로 쓴다(메모리는 결정론, 단순 비교).
        """
        counts: dict[str, int] = {}
        total = 0
        for doc in self._files.values():
            if not access_level.can_access(doc.access):
                continue
            if text and _lexical_score(text, doc) <= 0.0:
                continue
            total += 1
            counts[doc.source.value] = counts.get(doc.source.value, 0) + 1
        return total, counts

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
        from datetime import datetime, time, timedelta, timezone

        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=days_window)
            if days_window is not None and days_window > 0
            else None
        )
        # 명시 범위(ISO YYYY-MM-DD). days_window가 우선이라 그게 None일 때만 적용.
        from_dt = (
            datetime.combine(datetime.fromisoformat(date_from).date(), time.min, tzinfo=timezone.utc)
            if cutoff is None and date_from else None
        )
        to_dt = (
            datetime.combine(datetime.fromisoformat(date_to).date(), time.max, tzinfo=timezone.utc)
            if cutoff is None and date_to else None
        )
        type_norm = doc_type_filter.upper() if doc_type_filter else None
        hits: list[FileHit] = []
        for doc in self._files.values():
            # 1. 권한 하드 필터 — 점수 계산 전에 후보에서 제외(사후 필터 의존 금지).
            if not access_level.can_access(doc.access):
                continue
            # 2. source 필터.
            if source_filter is not None and doc.source != source_filter:
                continue
            # 2b. doc_type 필터.
            if type_norm is not None and (doc.doc_type or "").upper() != type_norm:
                continue
            # 2c. 날짜 필터(최근 N일 또는 명시 범위).
            if cutoff is not None and (doc.ingested_at is None or doc.ingested_at < cutoff):
                continue
            if from_dt is not None and (doc.ingested_at is None or doc.ingested_at < from_dt):
                continue
            if to_dt is not None and (doc.ingested_at is None or doc.ingested_at > to_dt):
                continue
            # 3. 어휘 점수 — 아무 필드도 안 맞으면(0) 결과에서 제외.
            score = _lexical_score(text, doc)
            if score <= 0.0:
                continue
            # 4. 부서 가중 — 권한 통과분에만 적용(순위만 조정).
            score += _department_boost(doc, department, department_boost_weight)
            hits.append(FileHit(file=doc, score=score))

        # 5. 점수 내림차순. 동점은 doc_id로 안정 tie-break(결정론).
        hits.sort(key=lambda hit: (-hit.score, hit.file.doc_id))
        return hits[:top_k]
