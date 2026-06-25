"""FieldExtractor 인터페이스 — 라인+스키마 → 추출 필드, 라인 → 스키마 제안.

구현(LLM 등)을 교체 가능하게 인터페이스 뒤로 분리한다. 서비스는 이 계약에만
의존한다(레이어 분리).
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from kms.domain.extraction import ExtractedField, ExtractionSchema, SchemaField, TextLine


class FieldExtractor(Protocol):
    """좌표 라인에서 스키마대로 값을 뽑고, 라인에서 스키마를 제안하는 계약."""

    def extract(
        self, lines: list[TextLine], schema: ExtractionSchema
    ) -> list[ExtractedField]:
        """스키마의 각 필드를 추출한다(값+근거 라인+bbox+신뢰도). 못 찾으면 값 None."""
        ...

    def propose_schema(
        self, lines: list[TextLine], *, doc_type: str | None = None
    ) -> list[SchemaField]:
        """문서 라인에서 추출 가치 있는 필드 후보를 제안한다(자동 스키마 생성)."""
        ...


class VlmFieldExtractor(Protocol):
    """비전 LLM(VLM) 기반 추출 — 페이지 이미지를 직접 보고 필드를 뽑는다.

    손글씨·스캔 문서용(텍스트 레이어가 없어 라인 추출이 불가). `FieldExtractor`와 달리
    라인이 아니라 파일 경로(→페이지 이미지)를 입력으로 받는다. 좌표(bbox)는 VLM이
    돌려주는 정규화 박스에서 변환한다. ⚠ 외부 전송이 따를 수 있다(구현별).
    """

    def is_available(self) -> bool:
        """의존(SDK·API 키)이 갖춰져 동작 가능한가."""
        ...

    def extract(self, path: Path, schema: ExtractionSchema) -> list[ExtractedField]:
        """페이지 이미지를 보고 스키마 필드를 추출한다(값+페이지+bbox+신뢰도)."""
        ...

    def propose_schema(
        self, path: Path, *, doc_type: str | None = None
    ) -> list[SchemaField]:
        """페이지 이미지를 보고 추출 필드 후보를 제안한다(자동 스키마)."""
        ...
