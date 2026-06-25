"""PostgreSQL + pgvector 벡터 저장소 (ADR-019) — OpenSearch k-NN 대체.

어휘 검색(PgSearchIndex)과 **같은 Postgres**(Supabase 무료 티어)를 쓰되 테이블은
분리한다. 임베딩은 `vector(dim)` 컬럼에 저장하고 코사인 거리(`<=>`)로 top_k를
검색한다. 권한·메타 필터는 `where` 방언을 SQL로 바꿔(`_pg_where`) retrieval 단계에
박는다(사후 필터 의존 금지). 벡터는 텍스트 리터럴(`'[..]'::vector`)로 바인딩하므로
pgvector 파이썬 패키지 의존 없이 psycopg만으로 동작한다.

`psycopg`는 메서드 호출 시 lazy import한다(모듈 import만으로 드라이버를 요구하지
않게 — 다른 backend 테스트 격리). DB 미도달·SQL 오류는 예외를 전파한다(조용한 실패
금지). 순수 헬퍼(DSN 정규화·벡터 리터럴·쿼리 빌드)는 DB 없이 단위 검증한다.
"""

from __future__ import annotations

import json
from typing import Any

from kms.adapters._pg_where import build_where_sql
from kms.adapters.ingestion.chunk.models import Chunk
from kms.adapters.vectorstore.base import chunk_to_meta
from kms.adapters.vectorstore.embedder import Embedder
from kms.config.settings import Settings

_CHUNK_TABLE = "kms_chunks"
_DIM_PROBE = "__dim_probe__"


class PgVectorStore:
    """pgvector 기반 벡터 저장소 — `VectorStore` 프로토콜(index/query/get/ping) 구현."""

    def __init__(
        self,
        dsn: str,
        embedder: Embedder,
        *,
        dim: int,
        table: str = _CHUNK_TABLE,
    ) -> None:
        self._dsn = normalize_dsn(dsn)
        self._embedder = embedder
        self._dim = dim
        self._table = table
        self._ensure_schema()

    @classmethod
    def from_settings(cls, settings: Settings, embedder: Embedder) -> "PgVectorStore":
        # 차원은 임베더로 1회 측정한다(테이블 vector(dim) 생성에 필요).
        dim = len(embedder.embed([_DIM_PROBE])[0])
        return cls(settings.database_url, embedder, dim=dim)

    # ── 부수효과 경계(DB) ────────────────────────────────────────────
    def _connect(self) -> Any:
        import psycopg

        return psycopg.connect(self._dsn, autocommit=True)

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            conn.execute(
                f"CREATE TABLE IF NOT EXISTS {self._table} ("
                "chunk_id text PRIMARY KEY, text text NOT NULL, "
                f"metadata jsonb NOT NULL, embedding vector({self._dim}) NOT NULL)"
            )

    def index(self, chunks: list[Chunk]) -> None:
        if not chunks:
            return
        vectors = self._embedder.embed([c.text for c in chunks])
        with self._connect() as conn, conn.cursor() as cur:
            for chunk, vec in zip(chunks, vectors, strict=True):
                cur.execute(
                    f"INSERT INTO {self._table}(chunk_id, text, metadata, embedding) "
                    "VALUES (%s, %s, %s, %s::vector) "
                    "ON CONFLICT (chunk_id) DO UPDATE SET "
                    "text = EXCLUDED.text, metadata = EXCLUDED.metadata, "
                    "embedding = EXCLUDED.embedding",
                    (
                        chunk.chunk_id,
                        chunk.text,
                        json.dumps(chunk_to_meta(chunk.metadata)),
                        vec_literal(vec),
                    ),
                )

    def query(
        self,
        embedding: list[float],
        top_k: int,
        where: dict[str, Any] | None = None,
    ) -> list[tuple[str, str, dict[str, Any], float]]:
        sql, params = build_query(self._table, embedding, top_k, where)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [(r[0], r[1], r[2], float(r[3])) for r in rows]

    def get(
        self, chunk_ids: list[str]
    ) -> list[tuple[str, str, dict[str, Any]]]:
        if not chunk_ids:
            return []
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT chunk_id, text, metadata FROM {self._table} "
                "WHERE chunk_id = ANY(%s)",
                (list(chunk_ids),),
            )
            rows = cur.fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    def ping(self) -> None:
        with self._connect() as conn:
            conn.execute("SELECT 1")

    def delete_by_source_url(self, source_url: str) -> int:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"DELETE FROM {self._table} WHERE metadata->>'source_url' = %s",
                (source_url,),
            )
            return cur.rowcount


# ── 순수 헬퍼(DB 불요 — 단위 테스트 대상) ─────────────────────────────
def normalize_dsn(dsn: str) -> str:
    """SQLAlchemy 스타일(`postgresql+psycopg://`)을 psycopg DSN(`postgresql://`)으로."""
    return dsn.replace("postgresql+psycopg://", "postgresql://", 1)


def vec_literal(embedding: list[float]) -> str:
    """임베딩을 pgvector 리터럴 문자열 `[v1,v2,...]`로 직렬화(바인딩용)."""
    return "[" + ",".join(repr(float(x)) for x in embedding) + "]"


def build_query(
    table: str,
    embedding: list[float],
    top_k: int,
    where: dict[str, Any] | None,
) -> tuple[str, list[Any]]:
    """top_k 코사인 검색 SQL + params 생성(값은 전부 %s 바인딩).

    score = `1 - 코사인거리`(클수록 관련도 높음 — base 계약). 권한·메타 필터는
    `where`를 SQL로 변환해 ORDER BY 전에 적용한다.
    """
    vec = vec_literal(embedding)
    params: list[Any] = [vec]
    where_sql = "TRUE"
    if where:
        where_params: list[Any] = []
        where_sql = build_where_sql(where, where_params)
        params.extend(where_params)
    params.append(vec)
    params.append(top_k)
    sql = (
        "SELECT chunk_id, text, metadata, 1 - (embedding <=> %s::vector) AS score "
        f"FROM {table} WHERE {where_sql} "
        "ORDER BY embedding <=> %s::vector LIMIT %s"
    )
    return sql, params
