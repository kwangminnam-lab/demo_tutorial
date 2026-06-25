"""추출 스키마·결과 영속 repository — ORM ↔ 도메인 변환.

`AccountRepository`와 같은 패턴(세션 생성자 주입, ORM은 경계 밖으로 안 내보냄).
JSONB의 `fields`는 도메인 모델(SchemaField/ExtractedField) ↔ dict 목록으로 변환한다.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from kms.adapters.db.models import ExtractionResultRow, ExtractionSchemaRow
from kms.domain.extraction import (
    ExtractedField,
    ExtractionResult,
    ExtractionSchema,
    SchemaField,
)


class ExtractionRepository:
    """추출 스키마/결과 CRUD + 도메인 변환. 세션은 생성자로 주입한다."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # ── 스키마 ────────────────────────────────────────────────────────────
    def create_schema(self, schema: ExtractionSchema) -> ExtractionSchema:
        row = ExtractionSchemaRow(
            name=schema.name,
            doc_type=schema.doc_type,
            fields=[f.model_dump() for f in schema.fields],
            auto_generated=schema.auto_generated,
            created_by=schema.created_by,
        )
        self._session.add(row)
        self._session.flush()
        return self._to_schema(row)

    def get_schema(self, schema_id: int) -> ExtractionSchema | None:
        row = self._session.get(ExtractionSchemaRow, schema_id)
        return self._to_schema(row) if row is not None else None

    def list_schemas(self) -> list[ExtractionSchema]:
        rows = self._session.scalars(
            select(ExtractionSchemaRow).order_by(ExtractionSchemaRow.name)
        )
        return [self._to_schema(row) for row in rows]

    def update_schema(
        self, schema_id: int, schema: ExtractionSchema
    ) -> ExtractionSchema | None:
        """기존 스키마를 덮어쓴다(name/doc_type/fields/auto_generated). 없으면 None.

        id·created_by·created_at(정체성/이력)은 보존한다 — 추출 결과의 schema_id 참조 유지.
        """
        row = self._session.get(ExtractionSchemaRow, schema_id)
        if row is None:
            return None
        row.name = schema.name
        row.doc_type = schema.doc_type
        row.fields = [f.model_dump() for f in schema.fields]
        row.auto_generated = schema.auto_generated
        self._session.flush()
        return self._to_schema(row)

    def delete_schema(self, schema_id: int) -> bool:
        row = self._session.get(ExtractionSchemaRow, schema_id)
        if row is None:
            return False
        self._session.delete(row)
        self._session.flush()
        return True

    # ── 결과 ──────────────────────────────────────────────────────────────
    def save_result(self, result: ExtractionResult) -> ExtractionResult:
        row = ExtractionResultRow(
            doc_id=result.doc_id,
            schema_id=result.schema_id,
            fields=[f.model_dump() for f in result.fields],
            created_by=result.created_by,
        )
        self._session.add(row)
        self._session.flush()
        return self._to_result(row)

    def list_results_by_doc(self, doc_id: str) -> list[ExtractionResult]:
        rows = self._session.scalars(
            select(ExtractionResultRow)
            .where(ExtractionResultRow.doc_id == doc_id)
            .order_by(ExtractionResultRow.created_at.desc())
        )
        return [self._to_result(row) for row in rows]

    # ── 변환 ──────────────────────────────────────────────────────────────
    @staticmethod
    def _to_schema(row: ExtractionSchemaRow) -> ExtractionSchema:
        return ExtractionSchema(
            id=row.id,
            name=row.name,
            doc_type=row.doc_type,
            fields=[SchemaField.model_validate(f) for f in row.fields],
            auto_generated=row.auto_generated,
            created_by=row.created_by,
            created_at=row.created_at,
        )

    @staticmethod
    def _to_result(row: ExtractionResultRow) -> ExtractionResult:
        return ExtractionResult(
            id=row.id,
            doc_id=row.doc_id,
            schema_id=row.schema_id,
            fields=[ExtractedField.model_validate(f) for f in row.fields],
            created_by=row.created_by,
            created_at=row.created_at,
        )
