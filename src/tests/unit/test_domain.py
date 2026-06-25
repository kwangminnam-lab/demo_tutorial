"""도메인 모델·규칙 단위 테스트 (결정론, 외부 의존 없음)."""

import pytest
from pydantic import ValidationError

from kms.domain import (
    AccessLevel,
    Document,
    DocumentMetadata,
    FileDoc,
    FileHit,
    SourceType,
    UserContext,
    department_boost,
    is_visible_to,
)


def _meta(access: AccessLevel, department: str | None = None) -> DocumentMetadata:
    return DocumentMetadata(
        source=SourceType.ONEDRIVE,
        access=access,
        author_department=department,
    )


def _user(access: AccessLevel, department: str = "영업") -> UserContext:
    return UserContext(user_id="u1", department=department, access_level=access)


def test_access_level_president_can_access_employee_document() -> None:
    assert AccessLevel.사장.can_access(AccessLevel.임직원) is True


def test_access_level_employee_cannot_access_president_document() -> None:
    assert AccessLevel.임직원.can_access(AccessLevel.사장) is False


def test_access_level_same_level_can_access() -> None:
    assert AccessLevel.임직원.can_access(AccessLevel.임직원) is True


def test_document_metadata_requires_source_and_access() -> None:
    with pytest.raises(ValidationError):
        DocumentMetadata()  # type: ignore[call-arg]


def test_document_metadata_requires_access() -> None:
    with pytest.raises(ValidationError):
        DocumentMetadata(source=SourceType.SLACK)  # type: ignore[call-arg]


def test_is_visible_to_respects_hierarchy() -> None:
    president_doc = _meta(AccessLevel.사장)
    employee = _user(AccessLevel.임직원)
    president = _user(AccessLevel.사장)

    assert is_visible_to(president_doc, employee) is False
    assert is_visible_to(president_doc, president) is True


def test_department_boost_same_department_returns_weight() -> None:
    meta = _meta(AccessLevel.임직원, department="영업")
    user = _user(AccessLevel.임직원, department="영업")
    assert department_boost(meta, user, weight=1.5) == 1.5


def test_department_boost_different_department_returns_zero() -> None:
    meta = _meta(AccessLevel.임직원, department="개발")
    user = _user(AccessLevel.임직원, department="영업")
    assert department_boost(meta, user, weight=1.5) == 0.0


def test_department_boost_none_department_returns_zero() -> None:
    meta = _meta(AccessLevel.임직원, department=None)
    user = _user(AccessLevel.임직원, department="영업")
    assert department_boost(meta, user, weight=1.5) == 0.0


def test_document_construction() -> None:
    doc = Document(doc_id="d1", content="hello", metadata=_meta(AccessLevel.임직원))
    assert doc.doc_id == "d1"
    assert doc.metadata.source is SourceType.ONEDRIVE


def test_filedoc_requires_source_and_access() -> None:
    with pytest.raises(ValidationError):
        FileDoc(doc_id="f1", title="report")  # type: ignore[call-arg]


def test_filedoc_requires_doc_id_and_title() -> None:
    with pytest.raises(ValidationError):
        FileDoc(source=SourceType.SLACK, access=AccessLevel.임직원)  # type: ignore[call-arg]


def test_filedoc_preserves_fields_and_defaults() -> None:
    file = FileDoc(
        doc_id="f1",
        title="2024 요금정책.pdf",
        source=SourceType.ONEDRIVE,
        access=AccessLevel.사장,
        author="남길동",
        author_department="영업",
        doc_type="PDF",
    )
    assert file.doc_id == "f1"
    assert file.title == "2024 요금정책.pdf"
    assert file.source is SourceType.ONEDRIVE
    assert file.access is AccessLevel.사장
    assert file.author_department == "영업"
    assert file.doc_type == "PDF"
    assert file.description == ""
    assert file.tags == []


def test_filehit_pairs_file_and_score() -> None:
    file = FileDoc(
        doc_id="f1",
        title="report",
        source=SourceType.SLACK,
        access=AccessLevel.임직원,
    )
    hit = FileHit(file=file, score=0.87)
    assert hit.file.doc_id == "f1"
    assert hit.score == 0.87
