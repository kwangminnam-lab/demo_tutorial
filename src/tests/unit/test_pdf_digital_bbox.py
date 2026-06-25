"""pdf_digital `_normalize_bbox` 단위 테스트 — docling bbox → 정규화 top-left.

코드 hover → 원본 문단/표 위치 강조의 기반. docling BBox(BOTTOMLEFT/TOPLEFT)를 페이지
크기 대비 [x, y, w, h] ∈ [0,1] top-left로 변환한다. 실 docling 불요 — 더블 객체로 검증.
"""

from __future__ import annotations

from types import SimpleNamespace

from kms.adapters.ingestion.extract.pdf_digital import _normalize_bbox


def _bbox(left: float, top: float, right: float, bottom: float, origin: str) -> object:
    return SimpleNamespace(l=left, t=top, r=right, b=bottom, coord_origin=origin)


def test_bottomleft_converts_to_topleft_normalized() -> None:
    # 페이지 100x200. BOTTOMLEFT: t=180(위쪽), b=160 → top-from-top=20, height=20.
    box = _bbox(10, 180, 60, 160, "BOTTOMLEFT")
    norm = _normalize_bbox(box, (100.0, 200.0))
    assert norm == (0.1, 0.1, 0.5, 0.1)  # x=10/100, y=(200-180)/200, w=50/100, h=20/200


def test_topleft_normalized() -> None:
    # TOPLEFT: t=20(위), b=40 → y=20/200, height=20.
    box = _bbox(10, 20, 60, 40, "TOPLEFT")
    norm = _normalize_bbox(box, (100.0, 200.0))
    assert norm == (0.1, 0.1, 0.5, 0.1)


def test_none_inputs_return_none() -> None:
    assert _normalize_bbox(None, (100.0, 200.0)) is None
    assert _normalize_bbox(_bbox(0, 1, 1, 0, "BOTTOMLEFT"), None) is None


def test_degenerate_box_returns_none() -> None:
    # 폭/높이 0 → None(그릴 박스 없음).
    assert _normalize_bbox(_bbox(10, 20, 10, 20, "TOPLEFT"), (100.0, 200.0)) is None


def test_values_clamped_to_unit_range() -> None:
    # 페이지 밖으로 넘치는 좌표(TOPLEFT, t<b 유지) → 0~1로 클램프.
    box = _bbox(-5, 20, 120, 250, "TOPLEFT")
    norm = _normalize_bbox(box, (100.0, 200.0))
    assert norm is not None
    assert all(0.0 <= v <= 1.0 for v in norm)
