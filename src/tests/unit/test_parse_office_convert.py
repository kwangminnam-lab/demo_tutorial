"""parse `_docling_extract`의 Office→PDF 변환 라우팅 단위 테스트.

DOCX 등 reflowable 문서는 docling이 페이지·좌표를 못 준다. LibreOffice로 PDF 변환 후
docling에 넘겨 페이지 경계 + bbox를 얻는다. 실제 soffice 불요 — 변환/파서를 더블로 가로채
"office는 변환 PDF로 파싱, PDF/이미지는 원본 그대로"만 결정론적으로 검증한다.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from kms.adapters.ingestion.ir import MarkdownDoc
from kms.api.v1 import parse as parse_api


def _patch_parser(monkeypatch, record: list[Path]) -> None:
    def fake_parse(path: Path) -> MarkdownDoc:
        record.append(path)
        return MarkdownDoc(markdown="본문", page_map=[(0, 1)], image_blobs={})

    monkeypatch.setattr(
        parse_api,
        "_DOCLING_PARSER",
        SimpleNamespace(is_available=lambda: True, parse=fake_parse),
    )
    monkeypatch.setattr(parse_api, "render_page_previews", lambda _p: {})


def test_docx_parsed_via_converted_pdf(monkeypatch, tmp_path: Path) -> None:
    record: list[Path] = []
    _patch_parser(monkeypatch, record)
    fake_pdf = tmp_path / "converted.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4")
    convert_calls: list[Path] = []

    def fake_convert(src: Path) -> Path:
        convert_calls.append(src)
        return fake_pdf

    monkeypatch.setattr(parse_api, "_convert_to_pdf", fake_convert)

    parse_api._docling_extract(".docx", b"docxbytes", render_previews=True)

    assert convert_calls, "office 문서는 PDF 변환을 거쳐야 한다"
    assert record == [fake_pdf]  # docling은 변환된 PDF로 파싱


def test_pdf_not_converted(monkeypatch) -> None:
    record: list[Path] = []
    _patch_parser(monkeypatch, record)
    converted = False

    def fake_convert(_src: Path) -> Path | None:
        nonlocal converted
        converted = True
        return None

    monkeypatch.setattr(parse_api, "_convert_to_pdf", fake_convert)

    parse_api._docling_extract(".pdf", b"%PDF-1.4", render_previews=False)

    assert converted is False  # PDF는 변환 불필요
    assert len(record) == 1 and record[0].suffix == ".pdf"


def test_office_convert_fallback_when_soffice_absent(monkeypatch) -> None:
    # soffice 부재(_convert_to_pdf=None) → 원본 docx로 파싱(graceful 폴백).
    record: list[Path] = []
    _patch_parser(monkeypatch, record)
    monkeypatch.setattr(parse_api, "_convert_to_pdf", lambda _src: None)

    parse_api._docling_extract(".docx", b"docxbytes", render_previews=False)

    assert len(record) == 1 and record[0].suffix == ".docx"
