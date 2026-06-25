"""어휘(키워드) 검색 인덱스 어댑터 — 파일 단위 통합 검색 (memory / pgvector tsvector).

`SearchIndex` 프로토콜 뒤로 두 구현을 둔다: `InMemorySearchIndex`(테스트·dev 기본,
서버 불요)와 `PgSearchIndex`(운영, PostgreSQL tsvector). 의미 유사도(vectorstore
k-NN)와 분리된 어휘 검색 경계이며, 권한 인지(하드 필터)를 검색 단계에서 강제하고
부서 가중(soft boost)으로 순위만 조정한다.
"""

from kms.adapters.searchindex.base import SearchIndex
from kms.adapters.searchindex.memory_store import InMemorySearchIndex
from kms.adapters.searchindex.pg_store import PgSearchIndex

__all__ = [
    "SearchIndex",
    "InMemorySearchIndex",
    "PgSearchIndex",
]
