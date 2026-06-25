"""테스트용 인메모리 ExtractionRepository 더블 — DB 없이 서비스/ API 흐름 검증.

`ExtractionRepository`와 같은 메서드 계약(스키마 CRUD + 결과 저장/조회)을 dict로
구현한다. 도메인 모델을 그대로 보관·복제해 반환한다(영속 round-trip 모사).
"""

from __future__ import annotations

from datetime import UTC, datetime

from kms.domain.extraction import ExtractionResult, ExtractionSchema


class FakeExtractionRepository:
    """인메모리 추출 스키마/결과 저장소."""

    def __init__(self) -> None:
        self._schemas: dict[int, ExtractionSchema] = {}
        self._results: list[ExtractionResult] = []
        self._schema_seq = 0
        self._result_seq = 0

    # ── 스키마 ──
    def create_schema(self, schema: ExtractionSchema) -> ExtractionSchema:
        self._schema_seq += 1
        stored = schema.model_copy(
            update={"id": self._schema_seq, "created_at": datetime.now(UTC)}
        )
        self._schemas[self._schema_seq] = stored
        return stored

    def get_schema(self, schema_id: int) -> ExtractionSchema | None:
        return self._schemas.get(schema_id)

    def list_schemas(self) -> list[ExtractionSchema]:
        return sorted(self._schemas.values(), key=lambda s: s.name)

    def update_schema(
        self, schema_id: int, schema: ExtractionSchema
    ) -> ExtractionSchema | None:
        existing = self._schemas.get(schema_id)
        if existing is None:
            return None
        # id·created_at(정체성) 보존, 나머지는 덮어쓴다(실 repo와 동일 계약).
        stored = schema.model_copy(
            update={"id": schema_id, "created_at": existing.created_at}
        )
        self._schemas[schema_id] = stored
        return stored

    def delete_schema(self, schema_id: int) -> bool:
        return self._schemas.pop(schema_id, None) is not None

    # ── 결과 ──
    def save_result(self, result: ExtractionResult) -> ExtractionResult:
        self._result_seq += 1
        stored = result.model_copy(update={"id": self._result_seq})
        self._results.append(stored)
        return stored

    def list_results_by_doc(self, doc_id: str) -> list[ExtractionResult]:
        return [r for r in self._results if r.doc_id == doc_id]
