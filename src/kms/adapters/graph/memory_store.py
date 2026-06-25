"""dict/set 기반 인메모리 그래프 저장소 — 테스트·dev 기본 경로.

Neo4j 서버·자격증명 없이 그래프 연산을 돌리기 위한 stub 구현(Phase 0 auth-stub
패턴과 동일). 실 그래프 DB의 의미(같은 문서·같은 source/author 연결, 권한 필터)를
순수 자료구조로 재현한다. 파생 데이터이므로 영속하지 않는다.
"""

from __future__ import annotations

from kms.adapters.ingestion.chunk.models import Chunk
from kms.domain.access import AccessLevel
from kms.domain.models import DocumentMetadata


class InMemoryGraphStore:
    """인메모리 문서·청크 그래프. `GraphStore` 프로토콜을 만족한다."""

    def __init__(self) -> None:
        # 문서 메타 (doc_id → DocumentMetadata).
        self._documents: dict[str, DocumentMetadata] = {}
        # 청크별 연결 속성 — related 탐색에 쓰는 최소 메타만 평탄화해 둔다.
        self._chunk_doc: dict[str, str] = {}
        self._chunk_source: dict[str, str] = {}
        self._chunk_author: dict[str, str | None] = {}
        self._chunk_access: dict[str, int] = {}
        # 결정론적 limit 절단을 위한 삽입 순서.
        self._chunk_order: list[str] = []
        # 사용자-콘텐츠 그래프 (Neo4j의 :User {email}-[:AUTHORED]->:Document 미러).
        # email → department  ·  email → {doc_ids}  ·  doc_id → author email
        self._users: dict[str, str | None] = {}
        self._user_docs: dict[str, set[str]] = {}
        self._doc_author: dict[str, str | None] = {}

    def add_document(self, doc_id: str, metadata: DocumentMetadata) -> None:
        self._documents[doc_id] = metadata
        # 사용자-콘텐츠 엣지: author가 있을 때만 등록 (Neo4j와 동일 조건).
        if metadata.author:
            self._users[metadata.author] = metadata.author_department
            self._user_docs.setdefault(metadata.author, set()).add(doc_id)
            self._doc_author[doc_id] = metadata.author
        else:
            self._doc_author[doc_id] = None

    def add_chunks(self, doc_id: str, chunks: list[Chunk]) -> None:
        for chunk in chunks:
            chunk_id = chunk.chunk_id
            meta = chunk.metadata
            if chunk_id not in self._chunk_doc:
                self._chunk_order.append(chunk_id)
            # PART_OF 관계 + 연결 속성 (source/author/access는 청크가 doc-level에서 상속).
            self._chunk_doc[chunk_id] = doc_id
            self._chunk_source[chunk_id] = meta.source.value
            self._chunk_author[chunk_id] = meta.author
            self._chunk_access[chunk_id] = int(meta.access)

    def delete(self, doc_id: str) -> None:
        self._documents.pop(doc_id, None)
        victims = [cid for cid, did in self._chunk_doc.items() if did == doc_id]
        for cid in victims:
            self._chunk_doc.pop(cid, None)
            self._chunk_source.pop(cid, None)
            self._chunk_author.pop(cid, None)
            self._chunk_access.pop(cid, None)
            if cid in self._chunk_order:
                self._chunk_order.remove(cid)
        author = self._doc_author.pop(doc_id, None)
        if author and author in self._user_docs:
            self._user_docs[author].discard(doc_id)

    def related(
        self,
        chunk_ids: list[str],
        user_access_level: AccessLevel,
        limit: int = 10,
    ) -> list[str]:
        seeds = set(chunk_ids)
        seed_docs = {self._chunk_doc[c] for c in seeds if c in self._chunk_doc}
        seed_sources = {self._chunk_source[c] for c in seeds if c in self._chunk_source}
        seed_authors = {
            self._chunk_author[c]
            for c in seeds
            if self._chunk_author.get(c) is not None
        }
        # 사용자-콘텐츠 확장: seed 문서들의 작성자(User) → 같은 User가 쓴 다른 문서들.
        seed_doc_authors = {
            author
            for doc_id in seed_docs
            if (author := self._doc_author.get(doc_id)) is not None
        }
        # 같은 부서 작성자 확장: User의 department 기준.
        seed_user_depts = {
            self._users.get(email)
            for email in seed_doc_authors
            if self._users.get(email) is not None
        }
        # User → AUTHORED → docs: 같은 User가 쓴 다른 문서들의 doc_id 집합.
        same_user_docs: set[str] = set()
        for email in seed_doc_authors:
            same_user_docs.update(self._user_docs.get(email, set()))
        # 같은 부서 User가 쓴 다른 문서들.
        same_dept_docs: set[str] = set()
        for email, dept in self._users.items():
            if dept in seed_user_depts:
                same_dept_docs.update(self._user_docs.get(email, set()))

        results: list[str] = []
        for chunk_id in self._chunk_order:
            if chunk_id in seeds:
                continue
            # 권한 인지 하드 필터: 사용자가 접근 못 하는 청크는 관계 확장에서도 제외.
            chunk_access = AccessLevel(self._chunk_access[chunk_id])
            if not user_access_level.can_access(chunk_access):
                continue
            author = self._chunk_author[chunk_id]
            chunk_doc = self._chunk_doc[chunk_id]
            connected = (
                chunk_doc in seed_docs
                or self._chunk_source[chunk_id] in seed_sources
                or (author is not None and author in seed_authors)
                # 사용자-콘텐츠: seed 문서의 author가 쓴 다른 문서의 청크
                or chunk_doc in same_user_docs
                # 같은 부서 작성자가 쓴 문서의 청크
                or chunk_doc in same_dept_docs
            )
            if connected:
                results.append(chunk_id)
                if len(results) >= limit:
                    break
        return results
