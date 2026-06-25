"""PgSearchIndex `build_search_sql` 단위 테스트 — SQL·params 순서·필터(DB 불요).

DB 상호작용은 Postgres 통합 테스트로 검증한다. 여기서는 순수 SQL 빌드만 잠근다:
점수식(ts_rank+부서가중), 권한 하드필터, source·날짜 필터, 빈 질의 처리, 파라미터
바인딩 순서.
"""

from __future__ import annotations

from typing import Any

from kms.adapters.searchindex.pg_store import build_search_sql

# 옵션 kwargs 묶음 — **_BASE/**opts 스플랫이 타입드 파라미터에 매칭되도록 dict[str, Any].
_BASE: dict[str, Any] = dict(
    source_filter=None,
    top_k=10,
    department=None,
    department_boost_weight=0.0,
    doc_type_filter=None,
    days_window=None,
    date_from=None,
    date_to=None,
)


def test_text_query_binds_rank_dept_access_match_topk() -> None:
    sql, params = build_search_sql("kms_files", "요금", 1, **_BASE)
    assert "ts_rank(tsv, plainto_tsquery('simple', %s))" in sql
    assert "tsv @@ plainto_tsquery('simple', %s)" in sql
    assert "access <= %s" in sql
    assert "ORDER BY score DESC LIMIT %s" in sql
    # 순서: rank텍스트, department(None), weight, access, match텍스트, top_k
    assert params == ["요금", None, 0.0, 1, "요금", 10]


def test_empty_query_drops_rank_and_match() -> None:
    sql, params = build_search_sql("kms_files", "", 2, **_BASE)
    assert "ts_rank" not in sql  # 빈 질의 → rank=0
    assert "tsv @@" not in sql  # 텍스트 매칭 조건 없음(전체 대상)
    # 순서: department(None), weight, access, top_k
    assert params == [None, 0.0, 2, 10]


def test_department_boost_and_source_filter() -> None:
    opts = {**_BASE, "department": "영업부", "department_boost_weight": 5.0}
    opts["source_filter"] = "onedrive"
    sql, params = build_search_sql("kms_files", "매출", 2, **opts)
    assert "CASE WHEN author_department = %s THEN %s ELSE 0 END" in sql
    assert "source = %s" in sql
    assert params == ["매출", "영업부", 5.0, 2, "매출", "onedrive", 10]


def test_days_window_uses_interval() -> None:
    opts = {**_BASE, "days_window": 30}
    sql, params = build_search_sql("kms_files", "", 2, **opts)
    assert "ingested_at >= now() - make_interval(days => %s)" in sql
    assert params == [None, 0.0, 2, 30, 10]


def test_date_range_filters() -> None:
    opts = {**_BASE, "date_from": "2026-01-01", "date_to": "2026-06-01"}
    sql, params = build_search_sql("kms_files", "", 1, **opts)
    assert "ingested_at >= %s" in sql and "ingested_at <= %s" in sql
    assert params == [None, 0.0, 1, "2026-01-01", "2026-06-01", 10]


def test_access_hard_filter_always_present() -> None:
    sql, _ = build_search_sql("t", "", 0, **_BASE)
    assert "access <= %s" in sql
