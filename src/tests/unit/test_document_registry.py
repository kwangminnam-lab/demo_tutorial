"""`kms.adapters.document_registry` 단위 테스트.

해소기는 순수 매핑(파일 콘텐츠 해시 ↔ 경로·메타)이라 단위 테스트로 잠근다:
doc_id 해소·파일명 별칭·미등록 KeyError·없는 파일 거부.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kms.adapters.document_registry import build_document_resolver, content_doc_id
from kms.domain.access import AccessLevel
from kms.domain.models import DocumentMetadata, SourceType


def _meta() -> DocumentMetadata:
    return DocumentMetadata(source=SourceType.SLACK, access=AccessLevel.임직원)


def test_resolves_by_content_doc_id(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("hello", encoding="utf-8")
    meta = _meta()
    resolver = build_document_resolver([(f, meta)])

    path, resolved = resolver(content_doc_id(f))
    assert path == f
    assert resolved is meta


def test_resolves_by_filename_alias(tmp_path: Path) -> None:
    f = tmp_path / "doc.txt"
    f.write_text("hi", encoding="utf-8")
    resolver = build_document_resolver([(f, _meta())])

    path, _ = resolver("doc.txt")
    assert path == f


def test_unknown_doc_id_raises_keyerror(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    resolver = build_document_resolver([(f, _meta())])

    with pytest.raises(KeyError):
        resolver("nonexistent")


def test_missing_file_rejected_at_build(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        build_document_resolver([(tmp_path / "ghost.txt", _meta())])


def test_filename_alias_matches_across_nfc_nfd(tmp_path: Path) -> None:
    """한글 파일명은 NFC/NFD 어느 형태로 조회해도 같은 항목으로 해소된다.

    macOS 파일명은 NFD로 저장되나 브라우저 입력은 NFC다 — 정규화 없으면 어긋난다.
    """
    import unicodedata

    name_nfc = unicodedata.normalize("NFC", "라이선스.txt")
    name_nfd = unicodedata.normalize("NFD", "라이선스.txt")
    assert name_nfc != name_nfd  # 두 형태가 실제로 다름을 확인

    f = tmp_path / name_nfd
    f.write_text("v", encoding="utf-8")
    resolver = build_document_resolver([(f, _meta())])

    assert resolver(name_nfc)[0] == f
    assert resolver(name_nfd)[0] == f


def test_doc_id_matches_ingestion_hash(tmp_path: Path) -> None:
    """doc_id는 적재(`ingestion_service._content_hash`)와 동일한 sha256(bytes)."""
    import hashlib

    f = tmp_path / "a.bin"
    f.write_bytes(b"\x00\x01\x02content")
    assert content_doc_id(f) == hashlib.sha256(f.read_bytes()).hexdigest()
