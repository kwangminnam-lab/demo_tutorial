"""pdf_digital `_split_pages_by_sentinel` 단위 테스트 — 정확한 페이지 경계 산출.

회귀: 이전엔 docling pages를 균등분할로 근사하거나 [(0,1)]로 떨어져 전 페이지가
1로 뭉치는 버그가 있었다. sentinel 분할은 정제 markdown에서 각 페이지의 정확한
시작 오프셋을 page_map으로 돌려준다(docling 불요 — 순수 문자열 함수).
"""

from __future__ import annotations

from kms.adapters.ingestion.extract.pdf_digital import _split_pages_by_sentinel

_SEP = "\x00\x00DOCUX_PAGE_BREAK\x00\x00"


def test_no_sentinel_is_single_page() -> None:
    md, page_map = _split_pages_by_sentinel("한 페이지 본문", _SEP)
    assert md == "한 페이지 본문"
    assert page_map == [(0, 1)]


def test_multi_page_offsets_are_exact() -> None:
    raw = f"1쪽{_SEP}2쪽 내용{_SEP}3쪽"
    md, page_map = _split_pages_by_sentinel(raw, _SEP)
    # sentinel은 빈 줄(\n\n)로 치환되어 페이지가 이어붙는다.
    assert md == "1쪽\n\n2쪽 내용\n\n3쪽"
    # 각 페이지 시작 오프셋이 정제 markdown에서 실제 위치와 일치("1쪽"=2자 + "\n\n"=2자).
    assert page_map == [(0, 1), (4, 2), (11, 3)]
    for offset, page in page_map:
        assert md[offset : offset + 1]  # 오프셋이 본문 범위 안
    # 2쪽은 1-base, 정제 markdown[5:]가 "2쪽..."에서 시작.
    assert md[page_map[1][0]:].startswith("2쪽")
    assert md[page_map[2][0]:].startswith("3쪽")
