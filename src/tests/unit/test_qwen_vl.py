"""QwenVlmExtractor 단위 테스트 — box_2d→PDF bbox 환산, JSON 파싱, is_available.

vLLM 호출은 `responder`(카나리 응답 콜백)로 주입해 네트워크 없이 검증한다. PDF는
pymupdf로 생성해 페이지 크기 기반 좌표 환산을 본다. (사내 Qwen3-VL = Gemini 대체.)
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

from kms.adapters.extraction.qwen_vl import QwenVlmExtractor
from kms.domain.extraction import ExtractionSchema, SchemaField


def _responder(text: str):  # type: ignore[no-untyped-def]
    """prompt/pages 무시하고 고정 응답을 주는 카나리."""
    return lambda _prompt, _pages: text


def _pdf(path: Path) -> tuple[float, float]:
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


class _RecordingSink:
    """OcrMetricsSink — log_ocr_run 인자를 캡처한다."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def log_ocr_run(
        self,
        *,
        run_name: str,
        params: dict[str, object],
        metrics: dict[str, float],
        tags: dict[str, str],
    ) -> None:
        self.calls.append(
            {"run_name": run_name, "params": params, "metrics": metrics, "tags": tags}
        )


def test_extract_logs_ocr_metrics(tmp_path: Path) -> None:
    pdf = tmp_path / "hand.pdf"
    _pdf(pdf)
    # 2필드 중 1개만 값 채움 → fill_rate=0.5.
    response = (
        '{"fields":['
        '{"key":"서명","value":"홍길동","page":1,"bbox":[50,100,300,200],"confidence":0.8},'
        '{"key":"금액","value":null,"page":1,"bbox":null,"confidence":null}'
        "]}"
    )
    sink = _RecordingSink()
    extractor = QwenVlmExtractor(
        None, "Qwen3-VL-4B-Instruct", responder=_responder(response), tracker=sink
    )
    extractor.extract(pdf, _schema())

    assert len(sink.calls) == 1
    call = sink.calls[0]
    assert call["run_name"] == "hand.pdf"
    metrics = call["metrics"]
    assert isinstance(metrics, dict)
    assert metrics["fields_total"] == 2.0
    assert metrics["fields_filled"] == 1.0
    assert metrics["fill_rate"] == 0.5
    assert metrics["total_s"] >= 0.0
    params = call["params"]
    assert isinstance(params, dict)
    assert params["model"] == "Qwen3-VL-4B-Instruct"
    assert params["field_count"] == 2


def test_is_available() -> None:
    # base_url 없음 + responder 없음 → 불가.
    assert QwenVlmExtractor(None, "Qwen3-VL-4B-Instruct").is_available() is False
    # base_url 설정 → 가능.
    assert QwenVlmExtractor("http://vllm:8000/v1", "m").is_available() is True
    # responder 주입 → 가능(키·URL 무관).
    assert QwenVlmExtractor(None, "m", responder=_responder("{}")).is_available() is True


def test_extract_converts_box_2d_to_pdf_bbox(tmp_path: Path) -> None:
    pdf = tmp_path / "hand.pdf"
    w, h = _pdf(pdf)
    response = (
        '{"fields":['
        '{"key":"서명","value":"홍길동","page":1,"bbox":[50,100,300,200],"confidence":0.8},'
        '{"key":"금액","value":"1,000,000","page":1,"bbox":[100,300,500,360],"confidence":0.92}'
        "]}"
    )
    extractor = QwenVlmExtractor(None, "m", responder=_responder(response))
    fields = extractor.extract(pdf, _schema())
    by_key = {f.key: f for f in fields}

    sig = by_key["서명"]
    assert sig.value == "홍길동"
    assert sig.source == "handwriting"
    assert sig.page == 1
    assert sig.bbox is not None
    x0, y0, x1, y1 = sig.bbox
    assert abs(x0 - 50 / 1000 * w) < 0.5
    assert abs(y0 - 100 / 1000 * h) < 0.5
    assert abs(x1 - 300 / 1000 * w) < 0.5
    assert abs(y1 - 200 / 1000 * h) < 0.5
    assert sig.needs_review is False  # conf 0.8 ≥ 0.6


def test_missing_field_becomes_empty(tmp_path: Path) -> None:
    pdf = tmp_path / "h.pdf"
    _pdf(pdf)
    response = '{"fields":[{"key":"서명","value":"x","page":1,"bbox":[10,10,20,20],"confidence":0.9}]}'
    extractor = QwenVlmExtractor(None, "m", responder=_responder(response))
    by_key = {f.key: f for f in extractor.extract(pdf, _schema())}
    assert by_key["금액"].value is None
    assert by_key["금액"].needs_review is True
    assert by_key["금액"].source == "handwriting"


def test_many_fields_are_chunked_and_merged(tmp_path: Path) -> None:
    # 필드 10개(>_CHUNK_SIZE=6) → 2개 묶음으로 동시 호출되고 결과가 병합돼야 한다.
    # responder가 프롬프트에 실린 키만 골라 응답 → 묶음별 응답을 합쳐 전 필드가 채워짐.
    pdf = tmp_path / "many.pdf"
    _pdf(pdf)
    keys = [f"필드{i}" for i in range(10)]
    schema = ExtractionSchema(name="s", fields=[SchemaField(key=k) for k in keys])

    calls: list[int] = []

    def responder(prompt: str, _pages: object) -> str:
        present = [k for k in keys if k in prompt]
        calls.append(len(present))
        items = ",".join(
            f'{{"key":"{k}","value":"v{k}","page":1,'
            f'"bbox":[10,10,200,40],"confidence":0.9}}'
            for k in present
        )
        return f'{{"fields":[{items}]}}'

    extractor = QwenVlmExtractor(None, "m", responder=responder)
    by_key = {f.key: f for f in extractor.extract(pdf, schema)}
    # 2개 묶음(6+4)으로 쪼개져 호출됨.
    assert len(calls) == 2
    assert sorted(calls) == [4, 6]
    # 전 필드가 병합돼 값이 채워짐.
    for k in keys:
        assert by_key[k].value == f"v{k}"


def test_key_match_is_whitespace_insensitive(tmp_path: Path) -> None:
    # VLM이 스키마 키와 공백만 다르게 돌려줘도 값이 매핑돼야 한다(빈칸 회귀 방지).
    pdf = tmp_path / "h.pdf"
    _pdf(pdf)
    schema = ExtractionSchema(name="s", fields=[SchemaField(key="보험 가입일")])
    response = (
        '{"fields":[{"key":"보험가입일","value":"2026-01-02","page":1,'
        '"bbox":[10,10,200,40],"confidence":0.9}]}'
    )
    extractor = QwenVlmExtractor(None, "m", responder=_responder(response))
    by_key = {f.key: f for f in extractor.extract(pdf, schema)}
    assert by_key["보험 가입일"].value == "2026-01-02"


def test_invalid_box_yields_no_bbox(tmp_path: Path) -> None:
    pdf = tmp_path / "h.pdf"
    _pdf(pdf)
    response = '{"fields":[{"key":"서명","value":"v","page":1,"confidence":0.9}]}'
    extractor = QwenVlmExtractor(None, "m", responder=_responder(response))
    fields = extractor.extract(pdf, ExtractionSchema(name="s", fields=[SchemaField(key="서명")]))
    assert fields[0].value == "v"
    assert fields[0].bbox is None


def test_unparseable_response(tmp_path: Path) -> None:
    pdf = tmp_path / "h.pdf"
    _pdf(pdf)
    extractor = QwenVlmExtractor(None, "m", responder=_responder("not json"))
    fields = extractor.extract(pdf, _schema())
    assert all(f.value is None and f.needs_review for f in fields)


def test_non_pdf_returns_empty(tmp_path: Path) -> None:
    txt = tmp_path / "a.txt"
    txt.write_text("hi")
    extractor = QwenVlmExtractor(None, "m", responder=_responder("{}"))
    fields = extractor.extract(txt, _schema())
    assert all(f.value is None for f in fields)


def _png(path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72.0, 72.0), "scan", fontsize=12)
    pix = page.get_pixmap()
    pix.save(str(path))
    doc.close()


def test_extract_from_image_scan(tmp_path: Path) -> None:
    img = tmp_path / "scan.png"
    _png(img)
    response = '{"fields":[{"key":"서명","value":"홍길동","page":1,"bbox":[50,100,300,200],"confidence":0.8}]}'
    extractor = QwenVlmExtractor(None, "m", responder=_responder(response))
    fields = extractor.extract(img, ExtractionSchema(name="s", fields=[SchemaField(key="서명")]))
    assert fields[0].value == "홍길동"
    assert fields[0].page == 1
    assert fields[0].bbox is not None


def test_propose_schema(tmp_path: Path) -> None:
    pdf = tmp_path / "h.pdf"
    _pdf(pdf)
    response = '{"fields":[{"key":"서명","type":"text","required":true},{"key":"날짜","type":"date"}]}'
    extractor = QwenVlmExtractor(None, "m", responder=_responder(response))
    by_key = {f.key: f for f in extractor.propose_schema(pdf, doc_type="수령증")}
    assert by_key["서명"].required is True
    assert by_key["날짜"].type == "date"
