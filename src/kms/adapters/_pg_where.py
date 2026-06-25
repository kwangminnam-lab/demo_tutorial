"""`where` 방언 → PostgreSQL SQL 조건 변환 (pgvector/pg 검색 어댑터 공용, ADR-019).

`vectorstore.base` 방언(InMemory·OpenSearch와 동일)을 `metadata jsonb` 컬럼 위
SQL `WHERE` 절로 바꾼다. 권한 인지(`{"access": {"$lte": N}}`)를 retrieval 단계에
박는 데 쓴다(사후 필터 의존 금지). 값은 psycopg `%s` 파라미터로 바인딩한다
(SQL injection 방지 — 리터럴 삽입 금지).

지원 방언:
- ``{"field": value}`` — 등호
- ``{"field": {"$lte"|"$gte"|"$lt"|"$gt"|"$ne"|"$eq": v}}``
- ``{"field": {"$in"|"$nin": [v, ...]}}``
- ``{"$and"|"$or": [clause, ...]}``

숫자 피연산자는 ``(metadata->>'f')::numeric``로 캐스팅 비교하고, 그 외(문자열)는
``metadata->>'f'`` 텍스트 비교한다. 필드명은 식별자 화이트리스트로 검증한다.
"""

from __future__ import annotations

import re
from typing import Any

_OPS: dict[str, str] = {
    "$lte": "<=",
    "$gte": ">=",
    "$lt": "<",
    "$gt": ">",
    "$ne": "!=",
    "$eq": "=",
}

# 메타 필드명 — 코드가 만든 키만 허용(영숫자·언더스코어). 식별자 주입 방지.
_FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def build_where_sql(where: dict[str, Any], params: list[Any]) -> str:
    """`where`를 SQL 조건문으로 변환하고 바인딩 값을 `params`에 순서대로 추가한다.

    반환 SQL은 `%s` 플레이스홀더만 포함한다(값은 전부 `params` 경유). 빈 조건은
    ``TRUE``를 반환한다(항상 참 — 필터 없음).
    """
    return _clause(where, params)


def _clause(where: dict[str, Any], params: list[Any]) -> str:
    parts: list[str] = []
    for key, cond in where.items():
        if key == "$and":
            parts.append("(" + " AND ".join(_clause(c, params) for c in cond) + ")")
        elif key == "$or":
            parts.append("(" + " OR ".join(_clause(c, params) for c in cond) + ")")
        else:
            parts.append(_field_clause(key, cond, params))
    return " AND ".join(parts) if parts else "TRUE"


def _field_clause(field: str, cond: Any, params: list[Any]) -> str:
    if not _FIELD_RE.match(field):
        raise ValueError(f"허용되지 않는 메타 필드명: {field!r}")
    if isinstance(cond, dict):
        sub: list[str] = []
        for op, operand in cond.items():
            if op in ("$in", "$nin"):
                sub.append(_in_clause(field, op, operand, params))
            elif op in _OPS:
                sub.append(f"{_col(field, operand)} {_OPS[op]} %s")
                params.append(operand)
            else:
                raise ValueError(f"지원하지 않는 연산자: {op}")
        return "(" + " AND ".join(sub) + ")"
    # 등호.
    sql = f"{_col(field, cond)} = %s"
    params.append(cond)
    return sql


def _in_clause(field: str, op: str, operand: Any, params: list[Any]) -> str:
    if not isinstance(operand, (list, tuple)):
        raise ValueError(f"{op}의 피연산자는 리스트여야 합니다: {operand!r}")
    sample = operand[0] if operand else ""
    negate = "NOT " if op == "$nin" else ""
    sql = f"{negate}({_col(field, sample)} = ANY(%s))"
    params.append(list(operand))
    return sql


def _col(field: str, sample: Any) -> str:
    """피연산자 타입에 맞춘 jsonb 접근자 — 숫자면 numeric 캐스팅, 그 외 텍스트."""
    if isinstance(sample, bool):
        return f"(metadata->>'{field}')::boolean"
    if isinstance(sample, (int, float)):
        return f"(metadata->>'{field}')::numeric"
    return f"metadata->>'{field}'"
