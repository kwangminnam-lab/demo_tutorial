"""필드추출 유스케이스 — 스키마 CRUD + 추출 + 근거 렌더 (ADR-024 phase 1).

조립: 라인 추출(`LineProviderRegistry`) → LLM 추출(`FieldExtractor`) → 영속
(`ExtractionRepository`). repo는 요청 세션에 묶여 요청별로 생성·주입된다(계정 서비스와
동일 패턴). registry/extractor는 무상태 공유.

레이어: 서비스는 어댑터 인터페이스(registry·extractor·repo)에만 의존한다. 파일 입출력
경로(임시 파일)는 API 경계가 다룬다 — 서비스는 `Path`를 받는다.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from kms.adapters.db.extraction_repository import ExtractionRepository
from kms.adapters.extraction.base import FieldExtractor, VlmFieldExtractor
from kms.adapters.ingestion.lines.registry import LineProviderRegistry
from kms.domain.extraction import (
    ExtractedField,
    ExtractionResult,
    ExtractionSchema,
)
from kms.services._evidence_render import render_field_evidence

logger = logging.getLogger(__name__)


class ExtractionService:
    """추출 스키마/결과 유스케이스. repo는 요청별, registry/extractor는 공유."""

    def __init__(
        self,
        repo: ExtractionRepository,
        registry: LineProviderRegistry,
        extractor: FieldExtractor,
        vlm_extractor: VlmFieldExtractor | None = None,
    ) -> None:
        self._repo = repo
        self._registry = registry
        self._extractor = extractor
        # 손글씨/스캔 추출기(Gemini 등). None이면 디지털만 — 손글씨 요청은 명확한 에러.
        self._vlm = vlm_extractor

    @property
    def vlm_available(self) -> bool:
        return self._vlm is not None and self._vlm.is_available()

    # ── 스키마 CRUD ───────────────────────────────────────────────────────
    def create_schema(self, schema: ExtractionSchema) -> ExtractionSchema:
        return self._repo.create_schema(schema)

    def list_schemas(self) -> list[ExtractionSchema]:
        return self._repo.list_schemas()

    def get_schema(self, schema_id: int) -> ExtractionSchema | None:
        return self._repo.get_schema(schema_id)

    def update_schema(
        self, schema_id: int, schema: ExtractionSchema
    ) -> ExtractionSchema | None:
        """기존 스키마를 수정한다. 없으면 None(라우트가 404)."""
        return self._repo.update_schema(schema_id, schema)

    def delete_schema(self, schema_id: int) -> bool:
        return self._repo.delete_schema(schema_id)

    # ── 자동 스키마 제안 (영속하지 않음) ──────────────────────────────────
    def propose_schema(
        self,
        path: Path,
        *,
        doc_type: str | None = None,
        name: str | None = None,
        prefer_vlm: bool = False,
    ) -> ExtractionSchema:
        """문서에서 추출 필드 후보를 제안한다(미영속 — 사용자 승인 후 create_schema).

        prefer_vlm이면 Gemini 비전으로 이미지에서 제안한다(손글씨/스캔). 미구성이면 에러.
        """
        if prefer_vlm:
            if not self.vlm_available:
                raise ValueError(
                    "VLM(사내 Qwen3-VL) 미구성 — VLM_BASE_URL 설정이 필요합니다(손글씨/스캔 모드)."
                )
            assert self._vlm is not None
            fields = self._vlm.propose_schema(path, doc_type=doc_type)
        else:
            # 디지털(pymupdf) → 스캔 OCR(paddle, 클러스터) 캐스케이드. 미지원/빈 결과면 [].
            lines = self._registry.extract_lines(path)
            fields = self._extractor.propose_schema(lines, doc_type=doc_type)
        if not fields:
            raise ValueError("문서에서 추출 필드를 제안하지 못했습니다(빈 결과).")
        return ExtractionSchema(
            name=name or (doc_type or "자동 스키마"),
            doc_type=doc_type,
            fields=fields,
            auto_generated=True,
        )

    # ── 추출 ──────────────────────────────────────────────────────────────
    def extract(
        self,
        path: Path,
        doc_id: str,
        schema: ExtractionSchema,
        *,
        created_by: str | None = None,
        prefer_vlm: bool = False,
    ) -> ExtractionResult:
        """문서를 스키마대로 추출하고 결과를 영속한다.

        경로 선택:
        - prefer_vlm=True(손글씨/스캔 모드): Gemini 비전으로 이미지 직접 추출. 미구성이면 에러.
        - 기본(디지털): pymupdf 라인 + 텍스트 LLM grounding. 라인이 0건(스캔본 가능)이고
          VLM이 구성돼 있으면 자동으로 VLM 폴백한다(로깅). VLM도 없으면 값 없음으로 채운다.
        """
        fields = self._extract_fields(path, doc_id, schema, prefer_vlm=prefer_vlm)
        result = ExtractionResult(
            doc_id=doc_id,
            schema_id=schema.id,
            fields=fields,
            created_by=created_by,
            created_at=datetime.now(UTC),
        )
        return self._repo.save_result(result)

    def _extract_fields(
        self, path: Path, doc_id: str, schema: ExtractionSchema, *, prefer_vlm: bool
    ) -> list[ExtractedField]:
        """경로 선택해 필드를 뽑는다(VLM 강제 / 디지털 + 빈결과 VLM 폴백)."""
        if prefer_vlm:
            if not self.vlm_available:
                raise ValueError(
                    "VLM(사내 Qwen3-VL) 미구성 — VLM_BASE_URL 설정이 필요합니다(손글씨/스캔 모드)."
                )
            assert self._vlm is not None
            return self._vlm.extract(path, schema)

        # 디지털(pymupdf) → 스캔 OCR(paddle, 클러스터) 캐스케이드. 둘 다 빈 결과면 [].
        lines = self._registry.extract_lines(path)
        if not lines:
            if self.vlm_available:
                logger.info(
                    "라인 0건 (doc_id=%s) — pymupdf/OCR 미검출 → VLM(Qwen3-VL) 폴백(스캔/손글씨).",
                    doc_id[:12],
                )
                assert self._vlm is not None
                return self._vlm.extract(path, schema)
            logger.warning(
                "추출 라인 0건 (doc_id=%s) — 디지털 텍스트 미검출(스캔/이미지), VLM 미구성. "
                "필드는 값 없음으로 채워집니다.",
                doc_id[:12],
            )
        return self._extractor.extract(lines, schema)

    def list_results(self, doc_id: str) -> list[ExtractionResult]:
        return self._repo.list_results_by_doc(doc_id)

    # ── 근거 렌더(B-Box) ──────────────────────────────────────────────────
    def render_evidence(
        self, path: Path, result: ExtractionResult
    ) -> dict[int, str]:
        """추출 필드 bbox를 페이지에 칠한 PNG data URL을 반환한다(디지털 PDF)."""
        return render_field_evidence(path, result.fields)
