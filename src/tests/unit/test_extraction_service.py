"""ExtractionService 단위 테스트 — 스키마 CRUD·추출 영속·자동제안 (fakes).

라인 추출/추출기/repo는 인메모리 더블. DB·LLM·pymupdf 없이 서비스 조립 로직만 검증.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kms.domain.extraction import (
    ExtractedField,
    ExtractionSchema,
    SchemaField,
    TextLine,
)
from kms.services.extraction_service import ExtractionService
from tests._fake_extraction_repo import FakeExtractionRepository


class _FakeRegistry:
    def __init__(self, lines: list[TextLine], supports: bool = True) -> None:
        self._lines = lines
        self._supports = supports

    def supports(self, path: Path) -> bool:
        return self._supports

    def resolve(self, path: Path):  # type: ignore[no-untyped-def]
        return self

    def lines(self, path: Path) -> list[TextLine]:
        return self._lines

    def extract_lines(self, path: Path) -> list[TextLine]:
        # 캐스케이드 결과 — 미지원이면 빈 라인(서비스가 VLM/값없음 폴백 판단).
        return self._lines if self._supports else []


class _FakeExtractor:
    def __init__(self, fields: list[ExtractedField], proposal: list[SchemaField]) -> None:
        self._fields = fields
        self._proposal = proposal

    def extract(self, lines, schema):  # type: ignore[no-untyped-def]
        return self._fields

    def propose_schema(self, lines, *, doc_type=None):  # type: ignore[no-untyped-def]
        return self._proposal


class _FakeVlm:
    def __init__(self, fields: list[ExtractedField], available: bool = True) -> None:
        self._fields = fields
        self._available = available
        self.extract_calls = 0

    def is_available(self) -> bool:
        return self._available

    def extract(self, path: Path, schema):  # type: ignore[no-untyped-def]
        self.extract_calls += 1
        return self._fields

    def propose_schema(self, path: Path, *, doc_type=None):  # type: ignore[no-untyped-def]
        return []


def _service(
    *,
    lines: list[TextLine] | None = None,
    fields: list[ExtractedField] | None = None,
    proposal: list[SchemaField] | None = None,
    vlm: _FakeVlm | None = None,
    registry_supports: bool = True,
) -> tuple[ExtractionService, FakeExtractionRepository]:
    repo = FakeExtractionRepository()
    svc = ExtractionService(
        repo,  # type: ignore[arg-type]
        _FakeRegistry(lines or [], supports=registry_supports),  # type: ignore[arg-type]
        _FakeExtractor(fields or [], proposal or []),  # type: ignore[arg-type]
        vlm,  # type: ignore[arg-type]
    )
    return svc, repo


def test_schema_crud_roundtrip() -> None:
    svc, _ = _service()
    created = svc.create_schema(
        ExtractionSchema(name="계약서", fields=[SchemaField(key="계약일")])
    )
    assert created.id is not None
    assert svc.get_schema(created.id) is not None
    assert [s.name for s in svc.list_schemas()] == ["계약서"]
    assert svc.delete_schema(created.id) is True
    assert svc.get_schema(created.id) is None
    assert svc.delete_schema(created.id) is False


def test_schema_update_overwrites_and_preserves_identity() -> None:
    svc, _ = _service()
    created = svc.create_schema(
        ExtractionSchema(name="계약서", fields=[SchemaField(key="계약일")])
    )
    assert created.id is not None

    updated = svc.update_schema(
        created.id,
        ExtractionSchema(
            name="계약서v2",
            doc_type="contract",
            fields=[SchemaField(key="금액", type="money")],
        ),
    )
    assert updated is not None
    assert updated.id == created.id  # 정체성(id) 보존 — 결과 schema_id 참조 유지
    assert updated.created_at == created.created_at
    assert updated.name == "계약서v2"
    assert updated.doc_type == "contract"
    assert [f.key for f in updated.fields] == ["금액"]
    # 조회도 갱신본을 돌려준다.
    got = svc.get_schema(created.id)
    assert got is not None and got.name == "계약서v2"


def test_schema_update_missing_returns_none() -> None:
    svc, _ = _service()
    missing = svc.update_schema(
        999, ExtractionSchema(name="x", fields=[SchemaField(key="a")])
    )
    assert missing is None


def test_extract_persists_result() -> None:
    fields = [ExtractedField(key="계약일", value="2026-03-01", confidence=0.9)]
    svc, repo = _service(
        lines=[TextLine(line_id=0, text="계약일 2026-03-01", page=1, bbox=(0, 0, 1, 1))],
        fields=fields,
    )
    schema = ExtractionSchema(id=7, name="s", fields=[SchemaField(key="계약일")])
    result = svc.extract(Path("x.pdf"), "deadbeef", schema, created_by="user@corp")

    assert result.id is not None
    assert result.doc_id == "deadbeef"
    assert result.schema_id == 7
    assert result.created_by == "user@corp"
    assert result.fields[0].value == "2026-03-01"
    # 영속됨 → doc_id로 조회 가능.
    assert len(svc.list_results("deadbeef")) == 1


def test_extract_empty_lines_still_persists() -> None:
    # 라인 0건이어도(스캔본) 추출기가 빈 필드를 주면 결과는 저장된다(조용한 누락 아님).
    fields = [ExtractedField(key="계약일", value=None, needs_review=True)]
    svc, _ = _service(lines=[], fields=fields)
    result = svc.extract(Path("blank.pdf"), "abc", ExtractionSchema(name="s", fields=[SchemaField(key="계약일")]))
    assert result.fields[0].needs_review is True


def test_propose_schema_returns_auto_generated() -> None:
    proposal = [SchemaField(key="계약일", type="date"), SchemaField(key="금액", type="money")]
    svc, _ = _service(lines=[TextLine(line_id=0, text="x", page=1, bbox=(0, 0, 1, 1))], proposal=proposal)
    schema = svc.propose_schema(Path("x.pdf"), doc_type="계약서")
    assert schema.auto_generated is True
    assert schema.doc_type == "계약서"
    assert [f.key for f in schema.fields] == ["계약일", "금액"]
    assert schema.id is None  # 미영속.


def test_propose_schema_empty_raises() -> None:
    svc, _ = _service(lines=[TextLine(line_id=0, text="x", page=1, bbox=(0, 0, 1, 1))], proposal=[])
    with pytest.raises(ValueError):
        svc.propose_schema(Path("x.pdf"))


def test_prefer_vlm_uses_vlm_extractor() -> None:
    vlm_fields = [ExtractedField(key="서명", value="홍길동", source="handwriting", confidence=0.9)]
    vlm = _FakeVlm(vlm_fields)
    # 디지털 라인이 있어도 prefer_vlm이면 VLM 경로.
    svc, _ = _service(
        lines=[TextLine(line_id=0, text="x", page=1, bbox=(0, 0, 1, 1))],
        fields=[ExtractedField(key="서명", value="디지털값")],
        vlm=vlm,
    )
    schema = ExtractionSchema(name="s", fields=[SchemaField(key="서명")])
    result = svc.extract(Path("h.pdf"), "abc", schema, prefer_vlm=True)
    assert vlm.extract_calls == 1
    assert result.fields[0].value == "홍길동"
    assert result.fields[0].source == "handwriting"


def test_prefer_vlm_without_vlm_raises() -> None:
    svc, _ = _service(lines=[], fields=[], vlm=None)
    with pytest.raises(ValueError, match="VLM"):
        svc.extract(Path("h.pdf"), "abc", ExtractionSchema(name="s", fields=[SchemaField(key="a")]), prefer_vlm=True)


def test_empty_digital_lines_falls_back_to_vlm() -> None:
    vlm_fields = [ExtractedField(key="a", value="vlm값", source="handwriting", confidence=0.8)]
    vlm = _FakeVlm(vlm_fields)
    # 디지털 라인 0건 + VLM 가용 → 자동 VLM 폴백.
    svc, _ = _service(lines=[], fields=[], vlm=vlm)
    result = svc.extract(Path("scan.pdf"), "abc", ExtractionSchema(name="s", fields=[SchemaField(key="a")]))
    assert vlm.extract_calls == 1
    assert result.fields[0].value == "vlm값"


def test_image_unsupported_lines_falls_back_to_vlm() -> None:
    # 이미지: line provider 미지원(supports=False) → 빈 라인 → VLM 폴백.
    vlm_fields = [ExtractedField(key="a", value="이미지값", source="handwriting", confidence=0.8)]
    vlm = _FakeVlm(vlm_fields)
    svc, _ = _service(lines=[], fields=[], vlm=vlm, registry_supports=False)
    result = svc.extract(Path("scan.png"), "abc", ExtractionSchema(name="s", fields=[SchemaField(key="a")]))
    assert vlm.extract_calls == 1
    assert result.fields[0].value == "이미지값"


def test_vlm_available_property() -> None:
    svc_with, _ = _service(vlm=_FakeVlm([], available=True))
    assert svc_with.vlm_available is True
    svc_off, _ = _service(vlm=_FakeVlm([], available=False))
    assert svc_off.vlm_available is False
    svc_none, _ = _service(vlm=None)
    assert svc_none.vlm_available is False
