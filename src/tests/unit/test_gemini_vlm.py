"""GeminiVlmExtractor 단위 테스트 — box_2d→PDF bbox 환산, JSON 파싱, is_available.

genai 클라이언트는 카나리 응답을 주는 더블로 주입(네트워크·키 없음). PDF는 pymupdf로
생성해 페이지 크기 기반 좌표 환산을 검증한다.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pymupdf

from kms.adapters.extraction.gemini_vlm import GeminiVlmExtractor
from kms.domain.extraction import ExtractionSchema, SchemaField


class _FakeModels:
    def __init__(self, text: str) -> None:
        self._text = text
        self.calls: list[dict[str, object]] = []

    def generate_content(self, *, model, contents, config):  # type: ignore[no-untyped-def]
        self.calls.append({"model": model, "contents": contents, "config": config})
        return SimpleNamespace(text=self._text)


class _FakeGenai:
    def __init__(self, text: str) -> None:
        self.models = _FakeModels(text)


def _pdf(path: Path) -> tuple[float, float]:
    """단일 페이지 PDF 생성. (너비, 높이) 반환(좌표 환산 기대값 계산용)."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72.0, 72.0), "sample", fontsize=12)
    w, h = float(page.rect.width), float(page.rect.height)
    doc.save(str(path))
    doc.close()
    return w, h


def _schema() -> ExtractionSchema:
    return ExtractionSchema(
        name="손글씨양식",
        fields=[SchemaField(key="서명"), SchemaField(key="금액", type="money")],
    )


def test_is_available() -> None:
    # 키 없음 → 불가.
    assert GeminiVlmExtractor(None, "gemini-2.5-flash").is_available() is False
    # 클라이언트 주입 → 가능(키 무관).
    assert GeminiVlmExtractor(None, "m", client=_FakeGenai("{}")).is_available() is True


def test_extract_converts_box_2d_to_pdf_bbox(tmp_path: Path) -> None:
    pdf = tmp_path / "hand.pdf"
    w, h = _pdf(pdf)
    response = (
        '{"fields":['
        '{"key":"서명","value":"홍길동","page":1,"box_2d":[100,50,200,300],"confidence":0.8},'
        '{"key":"금액","value":"1,000,000","page":1,"box_2d":[300,100,360,500],"confidence":0.92}'
        "]}"
    )
    extractor = GeminiVlmExtractor(None, "m", client=_FakeGenai(response))
    fields = extractor.extract(pdf, _schema())
    by_key = {f.key: f for f in fields}

    sig = by_key["서명"]
    assert sig.value == "홍길동"
    assert sig.source == "handwriting"
    assert sig.page == 1
    assert sig.bbox is not None
    # box_2d=[ymin,xmin,ymax,xmax]=[100,50,200,300] → x0=50/1000*w ... 근사 검증.
    x0, y0, x1, y1 = sig.bbox
    assert abs(x0 - 50 / 1000 * w) < 0.5
    assert abs(y0 - 100 / 1000 * h) < 0.5
    assert abs(x1 - 300 / 1000 * w) < 0.5
    assert abs(y1 - 200 / 1000 * h) < 0.5
    assert sig.needs_review is False  # conf 0.8 ≥ 0.6


def test_missing_field_becomes_empty(tmp_path: Path) -> None:
    pdf = tmp_path / "h.pdf"
    _pdf(pdf)
    response = '{"fields":[{"key":"서명","value":"x","page":1,"box_2d":[10,10,20,20],"confidence":0.9}]}'
    extractor = GeminiVlmExtractor(None, "m", client=_FakeGenai(response))
    fields = extractor.extract(pdf, _schema())
    by_key = {f.key: f for f in fields}
    assert by_key["금액"].value is None
    assert by_key["금액"].needs_review is True
    assert by_key["금액"].source == "handwriting"


def test_invalid_box_yields_no_bbox(tmp_path: Path) -> None:
    pdf = tmp_path / "h.pdf"
    _pdf(pdf)
    # box_2d 누락 → bbox None(값은 유지).
    response = '{"fields":[{"key":"서명","value":"v","page":1,"confidence":0.9}]}'
    extractor = GeminiVlmExtractor(None, "m", client=_FakeGenai(response))
    fields = extractor.extract(pdf, ExtractionSchema(name="s", fields=[SchemaField(key="서명")]))
    assert fields[0].value == "v"
    assert fields[0].bbox is None


def test_unparseable_response(tmp_path: Path) -> None:
    pdf = tmp_path / "h.pdf"
    _pdf(pdf)
    extractor = GeminiVlmExtractor(None, "m", client=_FakeGenai("not json"))
    fields = extractor.extract(pdf, _schema())
    assert all(f.value is None and f.needs_review for f in fields)


def test_non_pdf_returns_empty(tmp_path: Path) -> None:
    txt = tmp_path / "a.txt"
    txt.write_text("hi")
    extractor = GeminiVlmExtractor(None, "m", client=_FakeGenai("{}"))
    fields = extractor.extract(txt, _schema())
    assert all(f.value is None for f in fields)


def _png(path: Path) -> None:
    """단일 페이지 PDF를 PNG로 렌더해 이미지 파일로 저장(스캔 사진 모사)."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72.0, 72.0), "scan", fontsize=12)
    pix = page.get_pixmap()
    pix.save(str(path))
    doc.close()


def test_extract_from_image_scan(tmp_path: Path) -> None:
    # 스캔 이미지(PNG)도 추출 가능 — 내부에서 PDF로 정규화해 렌더.
    img = tmp_path / "scan.png"
    _png(img)
    response = '{"fields":[{"key":"서명","value":"홍길동","page":1,"box_2d":[100,50,200,300],"confidence":0.8}]}'
    extractor = GeminiVlmExtractor(None, "m", client=_FakeGenai(response))
    fields = extractor.extract(img, ExtractionSchema(name="s", fields=[SchemaField(key="서명")]))
    assert fields[0].value == "홍길동"
    assert fields[0].page == 1
    assert fields[0].bbox is not None  # 변환 PDF 페이지 크기로 좌표 환산됨.


def test_propose_schema(tmp_path: Path) -> None:
    pdf = tmp_path / "h.pdf"
    _pdf(pdf)
    response = '{"fields":[{"key":"서명","type":"text","required":true},{"key":"날짜","type":"date"}]}'
    extractor = GeminiVlmExtractor(None, "m", client=_FakeGenai(response))
    fields = extractor.propose_schema(pdf, doc_type="수령증")
    by_key = {f.key: f for f in fields}
    assert by_key["서명"].required is True
    assert by_key["날짜"].type == "date"
