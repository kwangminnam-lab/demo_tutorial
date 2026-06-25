"""그래프 저장소 어댑터 — 문서·청크 관계 기반 보강 검색 (Neo4j).

`GraphStore` 프로토콜 뒤로 두 구현을 둔다: `InMemoryGraphStore`(테스트·dev 기본,
서버 불요)와 `Neo4jGraphStore`(실구현). `related`는 권한 인지(access) 필터를
강제해 관계 확장으로 권한 밖 청크가 새지 않게 한다.
"""

from kms.adapters.graph.base import GraphStore
from kms.adapters.graph.memory_store import InMemoryGraphStore
from kms.adapters.graph.neo4j_store import Neo4jGraphStore

__all__ = [
    "GraphStore",
    "InMemoryGraphStore",
    "Neo4jGraphStore",
]
