"""`build_where_sql` 단위 테스트 — 방언→SQL 변환·파라미터 바인딩·주입 방어.

DB 없이 순수 변환을 검증한다. 값은 항상 params로 빠지고 SQL엔 %s만 남아야 한다.
"""

from __future__ import annotations

import pytest

from kms.adapters._pg_where import build_where_sql


def test_equality_text_uses_text_accessor() -> None:
    params: list[object] = []
    sql = build_where_sql({"source": "onedrive"}, params)
    assert sql == "metadata->>'source' = %s"
    assert params == ["onedrive"]


def test_numeric_operand_casts_to_numeric() -> None:
    params: list[object] = []
    sql = build_where_sql({"access": {"$lte": 2}}, params)
    assert sql == "((metadata->>'access')::numeric <= %s)"
    assert params == [2]


def test_in_clause_uses_any_array_param() -> None:
    params: list[object] = []
    sql = build_where_sql({"source": {"$in": ["slack", "onedrive"]}}, params)
    assert sql == "((metadata->>'source' = ANY(%s)))"
    assert params == [["slack", "onedrive"]]


def test_nin_negates() -> None:
    params: list[object] = []
    sql = build_where_sql({"source": {"$nin": ["x"]}}, params)
    assert "NOT (" in sql
    assert params == [["x"]]


def test_and_or_nesting() -> None:
    params: list[object] = []
    where = {
        "$and": [
            {"access": {"$lte": 2}},
            {"$or": [{"source": "slack"}, {"source": "onedrive"}]},
        ]
    }
    sql = build_where_sql(where, params)
    assert " AND " in sql and " OR " in sql
    # 값은 등장 순서대로 params에.
    assert params == [2, "slack", "onedrive"]


def test_empty_where_is_true() -> None:
    params: list[object] = []
    assert build_where_sql({}, params) == "TRUE"
    assert params == []


def test_rejects_bad_field_name() -> None:
    # 식별자 화이트리스트 — 주입 시도 차단.
    with pytest.raises(ValueError, match="필드명"):
        build_where_sql({"a'; DROP TABLE x;--": "v"}, [])


def test_rejects_unknown_operator() -> None:
    with pytest.raises(ValueError, match="연산자"):
        build_where_sql({"access": {"$regex": ".*"}}, [])


def test_no_literal_values_in_sql() -> None:
    # 모든 값은 %s로 — SQL 문자열에 실제 값이 박히지 않아야 한다.
    params: list[object] = []
    sql = build_where_sql({"access": {"$lte": 2}, "source": "secret-val"}, params)
    assert "secret-val" not in sql
    assert "2" not in sql.replace("%s", "")
