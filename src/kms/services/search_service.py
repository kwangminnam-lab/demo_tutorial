"""통합 검색 유스케이스 — 권한 필터(하드) + 부서 가중 재랭킹(soft).

흐름(ARCHITECTURE 통합 검색·ADR-005 인가·ADR-013 부서 가중):

1. 쿼리를 임베딩한다.
2. **권한 필터를 retrieval 단계에 박는다** — `vectorstore.query`의 `where`에
   `access <= 사용자 레벨` 정수 비교(+ 선택적 `source` 필터)를 넘겨, 사용자
   권한 밖 청크는 **애초에 가져오지 않는다**. 사후 제거에 의존하지 않는다
   (CONVENTIONS: 권한을 retrieval 후 사후 필터링에만 의존 금지 — 유출 위험).
3. (보강) `graph_store.related`로 관계 청크를 확장한다. 그래프 쪽도 `access`
   필터를 강제하므로 권한 밖 청크는 확장에서도 새지 않는다.
4. **부서 가중 재랭킹** — 권한을 통과한 결과의 점수에만 `department_boost`를
   더해 재정렬한다. 가중은 노출 여부가 아니라 **순위만** 바꾼다. 가중치는
   하드코딩하지 않고 `Settings.department_boost_weight`를 쓴다.
5. 최종 방어로 `is_visible_to`를 한 번 더 적용한다(retrieval 보증의 이중 방어 —
   결과에 권한 밖 문서가 절대 없도록). 그래프 보강 청크에 `source` 필터도 일관
   적용한다(그래프는 source를 모르므로 여기서 거른다).
"""

from __future__ import annotations

from typing import Any

from kms.adapters.graph.base import GraphStore
from kms.adapters.reranker.base import Reranker
from kms.adapters.searchindex.base import SearchIndex
from kms.adapters.vectorstore.base import VectorStore
from kms.adapters.vectorstore.embedder import Embedder
from kms.config.settings import Settings
from kms.domain.models import (
    Document,
    DocumentMetadata,
    FileDoc,
    FileHit,
    SearchQuery,
    SearchResult,
    UserContext,
    department_boost,
    is_visible_to,
)

# 그래프 보강 청크는 의미 유사도 점수가 없으므로 중립 기준 점수로 시작해
# 부서 가중만 반영한다 — 벡터 유사 적중(보통 양의 점수)보다 아래에 자연히 정렬된다.
_GRAPH_AUGMENT_BASE_SCORE = 0.0


class SearchService:
    """권한 인지 + 부서 가중을 적용하는 통합 검색 유스케이스."""

    def __init__(
        self,
        vectorstore: VectorStore,
        graph_store: GraphStore,
        embedder: Embedder,
        settings: Settings,
        search_index: SearchIndex,
        reranker: Reranker | None = None,
    ) -> None:
        self._vectorstore = vectorstore
        self._graph_store = graph_store
        self._embedder = embedder
        self._settings = settings
        # 어휘(파일 단위) 검색 인덱스 — search_files가 위임하는 대상(의미검색과 분리).
        self._search_index = search_index
        # Cross-encoder reranker (옵션). settings.reranker_enabled가 True일 때만 적용.
        self._reranker = reranker

    def search(self, query: SearchQuery, user: UserContext) -> list[SearchResult]:
        """RRF 하이브리드 검색 — 벡터 유사도(k-NN) + BM25(OpenSearch) rank 융합.

        흐름:
          1) 질의 임베딩 → 벡터 저장소 top-k (권한 hard filter)
          2) BM25 → OpenSearch top-k (파일 단위, 권한 hard filter)
          3) 그래프 보강 — 관계 청크 확장
          4) **RRF(Reciprocal Rank Fusion)** — vec_rank + bm25_rank 융합 점수
          5) 부서 가중 soft boost + 최종 가시성 가드
        """
        embedding = self._embedder.embed([query.text])[0]
        rrf_k = self._settings.rrf_k

        # 1) 벡터 유사도 — 벡터 저장소 top-k.
        where = self._build_where(query, user)
        primary = self._vectorstore.query(embedding, query.top_k, where)
        vector_ranks: dict[str, int] = {
            chunk_id: idx + 1
            for idx, (chunk_id, _t, _m, _s) in enumerate(primary)
        }
        seen = set(vector_ranks)

        # 2) BM25 — OpenSearch 파일 단위 top-k. doc_id별 rank 맵.
        bm25_doc_ranks: dict[str, int] = {}
        try:
            bm25_files = self._search_index.search(
                query.text,
                user.access_level,
                source_filter=query.source_filter,
                top_k=query.top_k,
                department=user.department,
                department_boost_weight=0.0,  # 부서 가중은 RRF 이후 별도 적용
                doc_type_filter=query.doc_type_filter,
                days_window=query.days_window,
                date_from=query.date_from,
                date_to=query.date_to,
            )
            for idx, file_hit in enumerate(bm25_files):
                bm25_doc_ranks[file_hit.file.doc_id] = idx + 1
        except Exception:  # noqa: BLE001 — BM25 실패는 RRF 둘 중 하나만으로 폴백.
            bm25_files = []

        # 3) 그래프 보강 — 관계 청크 id 확장(access 필터 포함).
        related_ids = self._graph_store.related(
            list(seen), user.access_level, limit=query.top_k
        )
        new_ids = [chunk_id for chunk_id in related_ids if chunk_id not in seen]
        augmented = self._vectorstore.get(new_ids)

        candidates: list[tuple[str, str, dict[str, Any]]] = [
            *((cid, text, meta) for cid, text, meta, _s in primary),
            *augmented,
        ]
        # BM25에는 잡혔으나 벡터·그래프에 못 잡힌 파일의 대표 청크도 후보로 추가.
        # source_url 기반으로 벡터 저장소에서 1건 끌어와 RRF에 합류시킨다.
        already_source_urls = {meta.get("source_url") for _cid, _t, meta in candidates}
        for file_hit in bm25_files:
            url = file_hit.file.source_url
            if not url or url in already_source_urls:
                continue
            extra = self._fetch_chunks_by_source_url(url, user, limit=1)
            candidates.extend(extra)
            already_source_urls.add(url)

        # BM25 source_url → rank 맵 (chunk source_url 룩업용, 루프 밖에서 1회 계산).
        bm25_url_rank = self._url_rank_map(bm25_files, bm25_doc_ranks)

        # 4·5) RRF 점수 + 부서 가중 + 권한·source 최종 방어.
        results: list[SearchResult] = []
        for chunk_id, text, meta in candidates:
            doc_meta = DocumentMetadata.model_validate(meta)
            if not is_visible_to(doc_meta, user):
                continue
            if query.source_filter is not None and doc_meta.source != query.source_filter:
                continue
            # 사용자가 비활성화한 커넥터의 source는 그래프 보강 등 어떤 경로로 들어와도 제외.
            if query.disabled_sources and doc_meta.source in query.disabled_sources:
                continue

            vec_part = (
                1.0 / (rrf_k + vector_ranks[chunk_id])
                if chunk_id in vector_ranks
                else 0.0
            )
            chunk_url = meta.get("source_url")
            bm25_part = (
                1.0 / (rrf_k + bm25_url_rank[chunk_url])
                if chunk_url and chunk_url in bm25_url_rank
                else 0.0
            )
            rrf_score = vec_part + bm25_part

            dept = department_boost(
                doc_meta, user, self._settings.department_boost_weight
            )
            # 부서 가중은 RRF 스케일(0~0.03)에 맞춰 축소 — 노출 영향 없음.
            score = rrf_score + dept * (1.0 / rrf_k)

            results.append(
                SearchResult(
                    document=Document(doc_id=chunk_id, content=text, metadata=doc_meta),
                    score=score,
                )
            )

        results.sort(key=lambda result: result.score, reverse=True)

        # 6) cross-encoder rerank (옵션). settings.reranker_enabled가 True이고 reranker
        #    인스턴스가 주입돼 있을 때만 적용. top-N에 대해서만 정밀 재정렬한다(비용↓).
        if self._settings.reranker_enabled and self._reranker is not None and results:
            top_n = max(1, self._settings.reranker_top_n)
            head = results[:top_n]
            tail = results[top_n:]
            passages = [r.document.content for r in head]
            ce_scores = self._reranker.score(query.text, passages)
            # cross-encoder 점수를 새로운 정렬 키로. 부서 가중은 작은 가산으로 유지.
            reranked = [
                SearchResult(
                    document=r.document,
                    score=ce + department_boost(
                        r.document.metadata, user,
                        self._settings.department_boost_weight,
                    ) * (1.0 / max(1, self._settings.rrf_k)),
                )
                for r, ce in zip(head, ce_scores)
            ]
            reranked.sort(key=lambda x: x.score, reverse=True)
            results = reranked + tail
        return results

    @staticmethod
    def _url_rank_map(bm25_files: list[Any], bm25_doc_ranks: dict[str, int]) -> dict[str, int]:
        """BM25 결과의 source_url → rank 맵 (chunk source_url 룩업용)."""
        return {
            f.file.source_url: bm25_doc_ranks[f.file.doc_id]
            for f in bm25_files
            if f.file.source_url and f.file.doc_id in bm25_doc_ranks
        }

    def _fetch_chunks_by_source_url(
        self, source_url: str, user: UserContext, *, limit: int = 1
    ) -> list[tuple[str, str, dict[str, Any]]]:
        """source_url 매칭 청크를 권한 필터와 함께 가져온다 (RRF의 BM25-only 보강)."""
        where: dict[str, Any] = {
            "$and": [
                {"source_url": source_url},
                {"access": {"$lte": int(user.access_level)}},
            ]
        }
        try:
            # query()는 임베딩이 필요하므로 0-벡터로 호출(점수 무관, 필터만 적용).
            # 차원은 임베더와 같아야 하므로 첫 embed 호출의 길이를 재사용한다.
            dim = len(self._embedder.embed([""])[0])
            zero = [0.0] * dim
            hits = self._vectorstore.query(zero, limit, where)
            return [(cid, text, meta) for cid, text, meta, _s in hits]
        except Exception:  # noqa: BLE001 — 보강 실패는 무시 (RRF 메인은 영향 없음).
            return []

    def facets_by_source(
        self, user: UserContext, *, text: str | None = None
    ) -> tuple[int, dict[str, int]]:
        """권한 인지 소스별 facets — 대시보드 커넥터 카운트 + 검색 결과 facets 공통.

        `text`가 None이면 권한 내 전체 코퍼스, 있으면 검색어 매칭 모집단의 통계.
        반환: `(total, {source_value: count})`.
        """
        return self._search_index.count_by_source(
            user.access_level, text=text
        )

    def get_file(self, doc_id: str, user: UserContext) -> FileDoc | None:
        """`doc_id`로 파일 메타를 반환한다. 권한 밖이면 None(서버가 단계에서 막음).

        파일 원본 접근(`GET /v1/files/{doc_id}`)의 권위 진입점이다. 권한 인지는
        retrieval 단계에서 강제한다(사후 필터 의존 금지, CONVENTIONS): 인덱스에
        문서가 있더라도 `access` 레벨이 사용자보다 높으면 **존재 자체를 노출하지
        않게** None을 돌린다(404 효과). 라우트는 None/존재를 그대로 404로 매핑한다.
        """
        doc = self._search_index.get(doc_id)
        if doc is None:
            return None
        if not user.access_level.can_access(doc.access):
            return None
        return doc

    def get_file_by_source_url(
        self, source_url: str, user: UserContext
    ) -> FileDoc | None:
        """`source_url`로 파일 메타 역조회. 권한 밖이면 None.

        RAG citation에서 chunk metadata의 source_url로 file을 찾아 다운로드 가능한
        file doc_id를 채우기 위함. `get_file`과 같은 권한 인지 규약 적용 — 권한 밖
        파일은 존재 자체를 노출하지 않는다.
        """
        if not source_url:
            return None
        doc = self._search_index.get_by_source_url(source_url)
        if doc is None:
            return None
        if not user.access_level.can_access(doc.access):
            return None
        return doc

    def search_files(self, query: SearchQuery, user: UserContext) -> list[FileHit]:
        """통합 검색 화면용 — 어휘(제목·설명) + 의미(본문 임베딩) 하이브리드 파일 검색.

        어휘 인덱스는 제목·설명·태그만 색인하므로 **본문에만 있는 단어**는 못 잡는다.
        그래서 챗봇과 동일한 벡터 검색으로 본문 매칭 청크를 찾아 source_url→파일로
        해소해 어휘 결과에 보강한다(추가 문서가 통합검색에도 뜨게). 권한 하드 필터는
        어휘는 인자로, 의미는 `_build_where`(access 필수)로 강제한다.
        """
        lexical = self._search_index.search(
            query.text,
            user.access_level,
            source_filter=query.source_filter,
            top_k=query.top_k,
            department=user.department,
            department_boost_weight=self._settings.department_boost_weight,
            doc_type_filter=query.doc_type_filter,
            days_window=query.days_window,
            date_from=query.date_from,
            date_to=query.date_to,
        )
        results: list[FileHit] = list(lexical)
        seen: set[str] = {hit.file.doc_id for hit in results}

        # 의미검색 보강 — 본문 임베딩 매칭 청크 → source_url → 파일. 권한은 where로 강제.
        try:
            embedding = self._embedder.embed([query.text])[0]
            where = self._build_where(query, user)
            for _cid, _text, meta, score in self._vectorstore.query(
                embedding, query.top_k, where
            ):
                url = meta.get("source_url")
                if not url:
                    continue
                doc = self._search_index.get_by_source_url(url)
                if doc is None or doc.doc_id in seen:
                    continue
                # 어휘가 적용하는 doc_type 필터는 의미 결과에도 일관 적용.
                if query.doc_type_filter and (
                    (doc.doc_type or "").upper() != query.doc_type_filter.upper()
                ):
                    continue
                seen.add(doc.doc_id)
                results.append(FileHit(file=doc, score=float(score)))
        except Exception:  # noqa: BLE001 — 의미검색 실패는 어휘 결과만으로 폴백.
            pass

        return results[: query.top_k]

    def _build_where(self, query: SearchQuery, user: UserContext) -> dict[str, Any]:
        """retrieval `where` 필터 구성 — 권한(필수) + source(선택) + 비활성 커넥터 제외.

        `access <= 사용자 레벨`을 항상 박고, `source_filter`가 있으면 AND로 묶는다.
        `where` 방언은 다중 조건에 `$and`를 쓰므로 조건 수에 따라 형태를 바꾼다.
        `disabled_sources`가 비어 있지 않으면 해당 source는 `$nin`으로 제외한다.
        """
        clauses: list[dict[str, Any]] = [
            {"access": {"$lte": int(user.access_level)}}
        ]
        if query.source_filter is not None:
            clauses.append({"source": query.source_filter.value})
        if query.disabled_sources:
            clauses.append(
                {"source": {"$nin": [s.value for s in query.disabled_sources]}}
            )
        if len(clauses) == 1:
            return clauses[0]
        return {"$and": clauses}
