"""parse.py SlideDeck 직렬화 회귀 테스트.

회귀: `Slide` 모델 필드는 `body`(text 아님). 과거 `_ir_to_dict`/`_ir_to_text`가
존재하지 않는 `slide.text`를 참조해 .pptx 파싱 시 AttributeError로 크래시했다.
이 테스트는 슬라이드 본문이 `body`에서 나오도록 잠근다.
"""

from __future__ import annotations

from kms.adapters.ingestion.ir import Slide, SlideDeck
from kms.api.v1.parse import _ir_to_dict, _ir_to_text


def _deck() -> SlideDeck:
    return SlideDeck(
        slides=[
            Slide(number=1, title="표지", body="회사 소개", notes=None),
            Slide(number=2, title=None, body="매출 현황", notes=None),
        ],
        image_blobs={},
    )


def test_ir_to_dict_slides_use_body() -> None:
    result = _ir_to_dict(_deck(), "deck.pptx", "PPTX")
    # type = 원본 문서 형식(PPTX), ir_kind = IR 표현 종류(SlideDeck).
    assert result["type"] == "PPTX"
    assert result["ir_kind"] == "SlideDeck"
    assert result["slides"] == [
        {"number": 1, "text": "회사 소개"},
        {"number": 2, "text": "매출 현황"},
    ]


def test_ir_to_text_slides_use_body() -> None:
    text = _ir_to_text(_deck())
    assert "회사 소개" in text
    assert "매출 현황" in text
