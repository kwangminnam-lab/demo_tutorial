"""벡터 저장소 어댑터 — 청크 임베딩 색인·의미 검색 (memory / pgvector k-NN).

임베딩은 `Embedder` 프로토콜 뒤로 격리해 실모델 없이 결정론 테스트가 가능하게
하고(`FakeEmbedder` 기본), 저장소는 backend 격리로 두 구현을 둔다:
`InMemoryVectorStore`(테스트·dev 기본, 서버 불요), `PgVectorStore`(운영, pgvector
k-NN). 둘은 같은 `VectorStore` 인터페이스 + 같은 `where` 방언을 만족한다. 권한
정책 판단은 search-service의 책임 — 여기서는 필터 메커니즘만 제공한다.
"""

from kms.adapters.vectorstore.base import VectorStore, chunk_to_meta
from kms.adapters.vectorstore.embedder import Embedder, FakeEmbedder
from kms.adapters.vectorstore.memory_store import InMemoryVectorStore
from kms.adapters.vectorstore.pg_store import PgVectorStore

__all__ = [
    "VectorStore",
    "InMemoryVectorStore",
    "PgVectorStore",
    "Embedder",
    "FakeEmbedder",
    "chunk_to_meta",
]
