"""kms.eval.ocr_eval 순수 로직 단위 테스트 — 정답 파싱·읽기순·CER/WER·IoU·faithfulness.

VLM/네트워크 없이 결정론적으로 검증한다(진입점 scripts/eval_ocr.py의 I/O는 제외).
"""

from __future__ import annotations

from kms.eval.ocr_eval import (
    LabelWord,
    cer,
    faithfulness,
    grounding,
    iou,
    parse_label,
    reading_order_text,
    value_in_text,
    wer,
)

_LABEL = {
    "Identifier": "bank_00001",
    "images": [{"width": 1000, "height": 1000, "document_name": "정보보안동의서"}],
    "annotations": [
        {
            "polygons": [
                # 둘째 줄 단어를 먼저 둬도 읽기순 정렬이 위→아래로 바로잡아야 한다.
                {"text": "금액", "points": [[100, 200], [100, 240], [180, 240], [180, 200]]},
                {"text": "계약일자", "points": [[100, 100], [100, 140], [200, 140], [200, 100]]},
                {"text": "2026", "points": [[220, 100], [220, 140], [320, 140], [320, 100]]},
            ]
        }
    ],
}


def test_parse_label_extracts_words_and_meta() -> None:
    doc = parse_label(_LABEL)
    assert doc.identifier == "bank_00001"
    assert doc.document_name == "정보보안동의서"
    assert (doc.width, doc.height) == (1000.0, 1000.0)
    assert len(doc.words) == 3
    # quad → axis-aligned (min/max).
    by_text = {w.text: w.bbox for w in doc.words}
    assert by_text["계약일자"] == (100.0, 100.0, 200.0, 140.0)


def test_reading_order_top_to_bottom_left_to_right() -> None:
    doc = parse_label(_LABEL)
    text = doc.full_text()
    # 첫 줄: 계약일자 2026 (좌→우), 둘째 줄: 금액.
    assert text == "계약일자 2026\n금액"
    assert reading_order_text([]) == ""


def test_cer_and_wer() -> None:
    # 완전 일치 → 0.
    assert cer("계약일자 2026", "계약일자 2026") == 0.0
    assert wer("계약 일자 2026", "계약 일자 2026") == 0.0
    # 1글자 오독 → CER = 1/공백제거정답길이. 정답 "계약일자2026"=8자.
    assert abs(cer("계약일자 2026", "계약일자 2025") - 1 / 8) < 1e-9
    # 단어 1개 틀림 → WER = 1/3.
    assert abs(wer("계약 일자 2026", "계약 일자 2025") - 1 / 3) < 1e-9
    # 정답 비고 가설 있음 → 1.0 (전부 오류).
    assert cer("", "abc") == 1.0
    assert cer("", "") == 0.0


def test_value_in_text_and_faithfulness() -> None:
    gt = "계약일자 2026-03-01 금액 1,200,000"
    assert value_in_text("2026-03-01", gt) is True
    assert value_in_text("금액 1,200,000", gt) is True  # 공백 무시 매칭(하이픈 등은 유지).
    assert value_in_text("홍길동", gt) is False
    # 값 3개 중 2개 실재 → 2/3, 평가대상 3.
    rate, n = faithfulness(["2026-03-01", "1,200,000", "홍길동", None], gt)
    assert n == 3
    assert abs(rate - 2 / 3) < 1e-9
    # 값 전무 → (0,0).
    assert faithfulness([None, None], gt) == (0.0, 0)


def test_iou_and_grounding() -> None:
    a = (0.0, 0.0, 1.0, 1.0)
    assert iou(a, a) == 1.0
    assert iou(a, (2.0, 2.0, 3.0, 3.0)) == 0.0  # 겹침 없음.
    # 절반 겹침: (0,0,1,1) ∩ (0.5,0,1.5,1) = 0.5 ; union = 1.5 → 1/3.
    assert abs(iou(a, (0.5, 0.0, 1.5, 1.0)) - 1 / 3) < 1e-9
    # grounding: 박스 2개 중 1개만 정답과 ≥임계 겹침.
    gt_boxes = [(0.0, 0.0, 1.0, 1.0)]
    mean_iou, grounded = grounding(
        [(0.0, 0.0, 1.0, 1.0), (5.0, 5.0, 6.0, 6.0)], gt_boxes, iou_threshold=0.3
    )
    assert abs(mean_iou - 0.5) < 1e-9  # (1.0 + 0.0)/2
    assert grounded == 0.5
    assert grounding([], gt_boxes) == (0.0, 0.0)


def test_normalized_boxes_scales_to_unit() -> None:
    doc = parse_label(
        {
            "Identifier": "x",
            "images": [{"width": 200, "height": 100}],
            "annotations": [
                {"polygons": [{"text": "a", "points": [[50, 25], [50, 75], [150, 75], [150, 25]]}]}
            ],
        }
    )
    assert doc.normalized_boxes() == [(0.25, 0.25, 0.75, 0.75)]


def test_label_word_dataclass_is_hashable() -> None:
    # frozen dataclass — set/딕셔너리 키로 쓸 수 있어야 한다(중복 제거 등).
    w = LabelWord("a", (0.0, 0.0, 1.0, 1.0))
    assert w in {w}
