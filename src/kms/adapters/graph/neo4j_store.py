"""Neo4j 그래프 저장소 어댑터 — `InMemoryGraphStore`와 같은 인터페이스의 실구현.

Settings의 `neo4j_*`로 드라이버를 만든다. 드라이버 생성은 lazy(연결은 첫 쿼리
시점)이므로 인스턴스화만으로는 서버에 붙지 않는다. **이 어댑터의 실서버 연결은
테스트하지 않는다** — 인터페이스 일치(프로토콜 만족)만 보장한다(ADR-007 어댑터 격리).
"""

from __future__ import annotations

from typing import Any

from neo4j import GraphDatabase

from kms.adapters.ingestion.chunk.models import Chunk
from kms.config.settings import Settings
from kms.domain.access import AccessLevel
from kms.domain.models import DocumentMetadata

# 관계 확장 — 권한(access) 허용분만 + 다음 중 하나로 연결:
#   (a) 같은 문서(PART_OF) 형제 청크
#   (b) 같은 source 다른 청크
#   (c) 같은 author 다른 청크 (chunk-level author 일치)
#   (d) ★ 사용자-콘텐츠 그래프 — seed 문서의 작성자(User)가 쓴 다른 문서의 청크.
#       (:User {email})-[:AUTHORED]->(:Document) 경로로 확장.
#   (e) ★ 같은 부서 작성자가 쓴 문서의 청크 (부서 단위 응집).
_RELATED_CYPHER = """
MATCH (seed:Chunk)
WHERE seed.chunk_id IN $chunk_ids
MATCH (seed)-[:PART_OF]->(sd:Document)
OPTIONAL MATCH (seed_user:User)-[:AUTHORED]->(sd)
WITH collect(DISTINCT sd) AS seed_docs,
     collect(DISTINCT seed.source) AS seed_sources,
     [a IN collect(DISTINCT seed.author) WHERE a IS NOT NULL] AS seed_chunk_authors,
     collect(DISTINCT seed_user) AS seed_users,
     [u IN collect(DISTINCT seed_user) WHERE u IS NOT NULL AND u.department IS NOT NULL
        | u.department] AS seed_depts
MATCH (c:Chunk)
WHERE NOT c.chunk_id IN $chunk_ids
  AND c.access <= $user_access
  AND (
    EXISTS { MATCH (c)-[:PART_OF]->(d:Document) WHERE d IN seed_docs }
    OR c.source IN seed_sources
    OR (c.author IS NOT NULL AND c.author IN seed_chunk_authors)
    OR EXISTS {
         MATCH (c)-[:PART_OF]->(d:Document)<-[:AUTHORED]-(u:User)
         WHERE u IN seed_users
       }
    OR EXISTS {
         MATCH (c)-[:PART_OF]->(d:Document)<-[:AUTHORED]-(u:User)
         WHERE u.department IN seed_depts
       }
  )
RETURN DISTINCT c.chunk_id AS chunk_id
LIMIT $limit
"""


class Neo4jGraphStore:
    """neo4j 드라이버 기반 그래프 저장소. `GraphStore` 프로토콜을 만족한다."""

    def __init__(
        self,
        uri: str,
        user: str,
        password: str,
        *,
        database: str | None = None,
    ) -> None:
        # 드라이버 생성은 lazy — 여기서 네트워크 연결이 일어나지 않는다.
        self._driver = GraphDatabase.driver(uri, auth=(user, password))
        self._database = database

    @classmethod
    def from_settings(cls, settings: Settings) -> "Neo4jGraphStore":
        """Settings의 `neo4j_*` 자격증명으로 인스턴스를 만든다."""
        return cls(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)

    def add_document(self, doc_id: str, metadata: DocumentMetadata) -> None:
        self._run(
            """
            MERGE (d:Document {doc_id: $doc_id})
            SET d.source = $source,
                d.author = $author,
                d.author_department = $author_department,
                d.access = $access
            """,
            doc_id=doc_id,
            source=metadata.source.value,
            author=metadata.author,
            author_department=metadata.author_department,
            access=int(metadata.access),
        )
        # 사용자-콘텐츠 그래프: author가 있으면 User 노드 + AUTHORED 관계 적재.
        # 같은 작성자가 쓴 다른 문서·같은 부서 작성자의 문서까지 RAG가 관계 확장으로 찾게 한다.
        if metadata.author:
            self._run(
                """
                MERGE (u:User {email: $email})
                SET u.department = $department
                WITH u
                MATCH (d:Document {doc_id: $doc_id})
                MERGE (u)-[:AUTHORED]->(d)
                """,
                email=metadata.author,
                department=metadata.author_department,
                doc_id=doc_id,
            )

    def add_chunks(self, doc_id: str, chunks: list[Chunk]) -> None:
        if not chunks:
            return
        rows = [
            {
                "chunk_id": chunk.chunk_id,
                "source": chunk.metadata.source.value,
                "author": chunk.metadata.author,
                "access": int(chunk.metadata.access),
            }
            for chunk in chunks
        ]
        self._run(
            # Neo4j 5는 MERGE 다음 UNWIND 사이에 WITH 절을 요구한다(문법 강제).
            """
            MERGE (d:Document {doc_id: $doc_id})
            WITH d
            UNWIND $chunks AS ch
            MERGE (c:Chunk {chunk_id: ch.chunk_id})
            SET c.source = ch.source, c.author = ch.author, c.access = ch.access
            MERGE (c)-[:PART_OF]->(d)
            """,
            doc_id=doc_id,
            chunks=rows,
        )

    def delete(self, doc_id: str) -> None:
        # 문서 + 그에 PART_OF로 연결된 청크를 DETACH DELETE(관계까지 제거).
        # User는 다른 문서를 가질 수 있어 남긴다.
        self._run(
            """
            MATCH (d:Document {doc_id: $doc_id})
            OPTIONAL MATCH (c:Chunk)-[:PART_OF]->(d)
            DETACH DELETE c, d
            """,
            doc_id=doc_id,
        )

    def related(
        self,
        chunk_ids: list[str],
        user_access_level: AccessLevel,
        limit: int = 10,
    ) -> list[str]:
        records = self._run(
            _RELATED_CYPHER,
            chunk_ids=chunk_ids,
            user_access=int(user_access_level),
            limit=limit,
        )
        return [record["chunk_id"] for record in records]

    def ping(self) -> None:
        """Neo4j 도달성 확인 — 연결 검증만 한다(헬스체크 ping 수준).

        쿼리를 돌리지 않고 드라이버의 연결 검증만 수행한다. 도달 불가면 예외를
        던진다(호출자가 잡아 degraded로 표기 — 헬스체크가 죽지 않게).
        """
        self._driver.verify_connectivity()

    def close(self) -> None:
        """드라이버 연결 풀을 닫는다."""
        self._driver.close()

    def _run(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        with self._driver.session(database=self._database) as session:
            result = session.run(cypher, **params)
            return [record.data() for record in result]
