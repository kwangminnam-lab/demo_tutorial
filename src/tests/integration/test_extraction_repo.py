"""ExtractionRepository 통합 테스트 — 실 PostgreSQL(JSONB) round-trip (ADR-024).

`TEST_DATABASE_URL`(없으면 `DATABASE_URL`) DSN의 PostgreSQL에 연결해 스키마/결과
CRUD와 도메인↔ORM(JSONB fields) 변환을 검증한다. DSN 미설정/미도달이면 skip.
테이블은 매 테스트 생성·삭제로 격리한다.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from kms.adapters.db.extraction_repository import ExtractionRepository
from kms.adapters.db.models import Base
from kms.domain.extraction import (
    ExtractedField,
    ExtractionResult,
    ExtractionSchema,
    SchemaField,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def session() -> Iterator[Session]:
    dsn = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not dsn or not dsn.startswith("postgresql"):
        pytest.skip("PostgreSQL DSN 미설정 — 통합 테스트 skip")
    try:
        engine = create_engine(dsn)
        with engine.connect():
            pass
    except Exception as exc:  # noqa: BLE001 — 연결 실패는 skip 사유.
        pytest.skip(f"PostgreSQL 미도달 — 통합 테스트 skip ({type(exc).__name__})")
    # 추출 테이블만 생성·삭제(다른 테이블 격리).
    tables = [
        Base.metadata.tables["extraction_schema"],
        Base.metadata.tables["extraction_result"],
    ]
    Base.metadata.drop_all(engine, tables=tables)
    Base.metadata.create_all(engine, tables=tables)
    maker = sessionmaker(bind=engine)
    try:
        with maker() as db_session:
            yield db_session
    finally:
        Base.metadata.drop_all(engine, tables=tables)


def test_schema_crud_roundtrip(session: Session) -> None:
    repo = ExtractionRepository(session)
    created = repo.create_schema(
        ExtractionSchema(
            name="계약서",
            doc_type="contract",
            fields=[
                SchemaField(key="계약일", type="date", description="체결일", required=True),
                SchemaField(key="금액", type="money"),
            ],
            created_by="master@corp",
        )
    )
    session.flush()
    assert created.id is not None

    fetched = repo.get_schema(created.id)
    assert fetched is not None
    assert fetched.name == "계약서"
    assert [f.key for f in fetched.fields] == ["계약일", "금액"]
    assert fetched.fields[0].required is True  # JSONB round-trip 보존.

    assert [s.name for s in repo.list_schemas()] == ["계약서"]
    assert repo.delete_schema(created.id) is True
    assert repo.get_schema(created.id) is None


def test_result_roundtrip_with_bbox(session: Session) -> None:
    repo = ExtractionRepository(session)
    saved = repo.save_result(
        ExtractionResult(
            doc_id="deadbeef",
            schema_id=1,
            fields=[
                ExtractedField(
                    key="금액",
                    value="1,200,000,000",
                    page=2,
                    bbox=(10.0, 20.0, 100.0, 32.0),
                    evidence_line_ids=[3, 4],
                    confidence=0.9,
                )
            ],
            created_by="user@corp",
        )
    )
    session.flush()
    assert saved.id is not None

    results = repo.list_results_by_doc("deadbeef")
    assert len(results) == 1
    field = results[0].fields[0]
    assert field.value == "1,200,000,000"
    assert field.page == 2
    assert field.bbox == (10.0, 20.0, 100.0, 32.0)  # tuple round-trip via JSONB.
    assert field.evidence_line_ids == [3, 4]
    assert repo.list_results_by_doc("other") == []
