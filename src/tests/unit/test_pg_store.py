"""PgVectorStore 순수 헬퍼 단위 테스트 — DSN 정규화·벡터 리터럴·쿼리 빌드(DB 불요).

DB 상호작용(index/query/get/ping)은 Postgres+pgvector가 필요해 별도 통합 테스트로
검증한다. 여기서는 네트워크 없이 결정론적인 순수 함수만 잠근다.
"""

from __future__ import annotations

from kms.adapters.vectorstore.pg_store import (
    build_query,
    normalize_dsn,
    vec_literal,
)


def test_normalize_dsn_strips_sqlalchemy_driver() -> None:
    assert (
        normalize_dsn("postgresql+psycopg://u:p@h:5432/db")
        == "postgresql://u:p@h:5432/db"
    )
    # 이미 psycopg DSN이면 그대로.
    assert normalize_dsn("postgresql://h/db") == "postgresql://h/db"


def test_vec_literal_format() -> None:
    assert vec_literal([0.1, 0.2, 0.3]) == "[0.1,0.2,0.3]"
    assert vec_literal([1, 2]) == "[1.0,2.0]"


def test_build_query_no_where() -> None:
    sql, params = build_query("kms_chunks", [0.1, 0.2], 5, None)
    assert "ORDER BY embedding <=> %s::vector LIMIT %s" in sql
    assert "WHERE TRUE" in sql
    # params: [score_vec, order_vec, top_k]
    assert params == ["[0.1,0.2]", "[0.1,0.2]", 5]


def test_build_query_with_where_binds_filter_between_vectors() -> None:
    sql, params = build_query(
        "kms_chunks", [0.5], 3, {"access": {"$lte": 2}}
    )
    assert "(metadata->>'access')::numeric <= %s" in sql
    # 순서: score_vec, where값(2), order_vec, top_k
    assert params == ["[0.5]", 2, "[0.5]", 3]


def test_build_query_select_shape() -> None:
    sql, _ = build_query("t", [0.0], 1, None)
    assert sql.startswith(
        "SELECT chunk_id, text, metadata, 1 - (embedding <=> %s::vector) AS score"
    )
