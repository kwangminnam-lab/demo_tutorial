"""PostgreSQL tsvector 어휘 검색 인덱스 (ADR-019) — OpenSearch Nori 대체.

파일 단위 메타(제목·설명·태그·작성자)를 `tsvector`로 색인해 `plainto_tsquery`로
검색한다. 한국어 형태소(Nori)가 없는 무료 스택이라 `'simple'` 구성(공백·구두점
토큰화)을 쓴다 — 형태소 분해는 없지만 키워드·구문 매칭은 동작한다(품질 차이는
ADR-019 트레이드오프). 권한 인지(`access <= level`)는 SQL WHERE에 박고(사후 필터
금지), 부서 가중은 점수에 더한다(순위만 변경).

`psycopg` lazy import, DB 오류 전파(조용한 실패 금지). search SQL 빌드는 순수
함수(`build_search_sql`)로 분리해 DB 없이 단위 검증한다. source_url은 NFC로
정규화해 저장·조회한다(macOS NFD ↔ NFC 불일치로 인한 조회 실패 방지).
"""

from __future__ import annotations

import unicodedata
from typing import Any

from kms.adapters.searchindex.base import FileHit
from kms.adapters.vectorstore.pg_store import normalize_dsn
from kms.domain.access import AccessLevel
from kms.domain.models import FileDoc, SourceType
from kms.config.settings import Settings

_FILE_TABLE = "kms_files"
_COLS = (
    "doc_id, title, description, tags, author, author_department, "
    "source, source_url, doc_type, ingested_at, access"
)


def _nfc(value: str | None) -> str | None:
    return unicodedata.normalize("NFC", value) if value else value


class PgSearchIndex:
    """tsvector 기반 어휘 검색 인덱스 — `SearchIndex` 프로토콜 구현."""

    def __init__(self, dsn: str, *, table: str = _FILE_TABLE) -> None:
        self._dsn = normalize_dsn(dsn)
        self._table = table
        self._ensure_schema()

    @classmethod
    def from_settings(cls, settings: Settings) -> "PgSearchIndex":
        return cls(settings.database_url)

    def _connect(self) -> Any:
        import psycopg

        return psycopg.connect(self._dsn, autocommit=True)

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            # generated 컬럼의 생성식은 IMMUTABLE이어야 한다. to_tsvector(...)를 식에
            # 바로 쓰면 일부 PG가 "generation expression is not immutable"로 거부하므로
            # IMMUTABLE로 표시한 래퍼로 감싼다(상수 config 'simple'이라 실제로 immutable).
            conn.execute(
                "CREATE OR REPLACE FUNCTION docux_search_tsv("
                "_title text, _description text, _tags text[]) RETURNS tsvector "
                "LANGUAGE sql IMMUTABLE AS $$ SELECT to_tsvector('simple', "
                "coalesce(_title,'') || ' ' || coalesce(_description,'') || ' ' || "
                "array_to_string(_tags, ' ')) $$"
            )
            conn.execute(
                f"CREATE TABLE IF NOT EXISTS {self._table} ("
                "doc_id text PRIMARY KEY, title text NOT NULL, "
                "description text NOT NULL DEFAULT '', "
                "tags text[] NOT NULL DEFAULT '{}', author text, "
                "author_department text, source text NOT NULL, source_url text, "
                "doc_type text, ingested_at timestamptz, access int NOT NULL, "
                "tsv tsvector GENERATED ALWAYS AS "
                "(docux_search_tsv(title, description, tags)) STORED)"
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS {self._table}_tsv_idx "
                f"ON {self._table} USING GIN(tsv)"
            )

    def index_file(self, doc: FileDoc) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {self._table} ({_COLS}) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (doc_id) DO UPDATE SET "
                "title=EXCLUDED.title, description=EXCLUDED.description, "
                "tags=EXCLUDED.tags, author=EXCLUDED.author, "
                "author_department=EXCLUDED.author_department, source=EXCLUDED.source, "
                "source_url=EXCLUDED.source_url, doc_type=EXCLUDED.doc_type, "
                "ingested_at=EXCLUDED.ingested_at, access=EXCLUDED.access",
                (
                    doc.doc_id, doc.title, doc.description, list(doc.tags),
                    doc.author, doc.author_department, doc.source.value,
                    _nfc(doc.source_url), doc.doc_type, doc.ingested_at,
                    int(doc.access),
                ),
            )

    def get(self, doc_id: str) -> FileDoc | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT {_COLS} FROM {self._table} WHERE doc_id = %s", (doc_id,)
            )
            row = cur.fetchone()
        return _row_to_doc(row) if row else None

    def get_by_source_url(self, source_url: str) -> FileDoc | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT {_COLS} FROM {self._table} WHERE source_url = %s LIMIT 1",
                (_nfc(source_url),),
            )
            row = cur.fetchone()
        return _row_to_doc(row) if row else None

    def delete(self, doc_id: str) -> bool:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(f"DELETE FROM {self._table} WHERE doc_id = %s", (doc_id,))
            return cur.rowcount > 0

    def count_by_source(
        self, access_level: AccessLevel, *, text: str | None = None
    ) -> tuple[int, dict[str, int]]:
        sql = f"SELECT source, count(*) FROM {self._table} WHERE access <= %s"
        params: list[Any] = [int(access_level)]
        if text:
            sql += " AND tsv @@ plainto_tsquery('simple', %s)"
            params.append(text)
        sql += " GROUP BY source"
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        by_source = {r[0]: int(r[1]) for r in rows}
        return sum(by_source.values()), by_source

    def search(
        self,
        text: str,
        access_level: AccessLevel,
        *,
        source_filter: SourceType | None = None,
        top_k: int = 10,
        department: str | None = None,
        department_boost_weight: float = 0.0,
        doc_type_filter: str | None = None,
        days_window: int | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[FileHit]:
        sql, params = build_search_sql(
            self._table, text, int(access_level),
            source_filter=source_filter.value if source_filter else None,
            top_k=top_k, department=department,
            department_boost_weight=department_boost_weight,
            doc_type_filter=doc_type_filter, days_window=days_window,
            date_from=date_from, date_to=date_to,
        )
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        # 마지막 컬럼(score)을 떼고 FileDoc 복원.
        return [FileHit(file=_row_to_doc(r[:-1]), score=float(r[-1])) for r in rows]

    def ping(self) -> None:
        with self._connect() as conn:
            conn.execute("SELECT 1")


def _row_to_doc(row: Any) -> FileDoc:
    return FileDoc(
        doc_id=row[0], title=row[1], description=row[2], tags=list(row[3] or []),
        author=row[4], author_department=row[5], source=SourceType(row[6]),
        source_url=row[7], doc_type=row[8], ingested_at=row[9],
        access=AccessLevel(row[10]),
    )


def build_search_sql(
    table: str,
    text: str,
    access_level: int,
    *,
    source_filter: str | None,
    top_k: int,
    department: str | None,
    department_boost_weight: float,
    doc_type_filter: str | None,
    days_window: int | None,
    date_from: str | None,
    date_to: str | None,
) -> tuple[str, list[Any]]:
    """어휘 검색 SQL + params 생성(값은 전부 %s 바인딩 — 순수, DB 불요).

    점수 = `ts_rank`(빈 질의면 0) + 부서 가중(작성자 부서 == 요청 부서면 +weight).
    권한 하드 필터(`access <= level`)와 source·doc_type·날짜 필터를 WHERE에 적용한다.
    빈 질의는 텍스트 매칭 조건을 생략(전체 대상, 부서 가중·필터만).
    """
    has_text = bool(text)
    rank = (
        "ts_rank(tsv, plainto_tsquery('simple', %s))" if has_text else "0"
    )
    score = (
        f"({rank} + CASE WHEN author_department = %s THEN %s ELSE 0 END) AS score"
    )
    params: list[Any] = []
    if has_text:
        params.append(text)
    params.append(department)
    params.append(department_boost_weight)

    where = ["access <= %s"]
    params.append(access_level)
    if has_text:
        where.append("tsv @@ plainto_tsquery('simple', %s)")
        params.append(text)
    if source_filter:
        where.append("source = %s")
        params.append(source_filter)
    if doc_type_filter:
        where.append("doc_type = %s")
        params.append(doc_type_filter)
    if days_window is not None:
        where.append("ingested_at >= now() - make_interval(days => %s)")
        params.append(days_window)
    if date_from:
        where.append("ingested_at >= %s")
        params.append(date_from)
    if date_to:
        where.append("ingested_at <= %s")
        params.append(date_to)

    params.append(top_k)
    sql = (
        f"SELECT {_COLS}, {score} FROM {table} "
        f"WHERE {' AND '.join(where)} ORDER BY score DESC LIMIT %s"
    )
    return sql, params
