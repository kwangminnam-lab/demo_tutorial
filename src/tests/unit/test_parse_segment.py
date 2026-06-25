"""parse `_segment_markdown` 단위 테스트 — 본문을 단락/섹션 단위로 분할.

회귀: JSON 본문이 통짜 markdown 한 덩어리로 내려가던 문제. heading이 있으면 섹션
단위, 없으면 단락(빈 줄) 단위로 항상 잘게 구분되고 각 레코드에 페이지가 태깅된다.
"""

from __future__ import annotations

from kms.api.v1.parse import _page_for_offset, _segment_markdown


def test_heading_doc_splits_into_sections() -> None:
    md = "# 1장\n\n본문 가\n\n## 1.1\n\n본문 나"
    page_map = [(0, 1)]
    recs = _segment_markdown(md, page_map)
    assert len(recs) >= 2
    titles = [r["title"] for r in recs]
    assert "1장" in titles
    assert all("text" in r and "page" in r and "index" in r for r in recs)


def test_flat_doc_falls_back_to_paragraph_blocks() -> None:
    # heading 없는 평문 → 단락(빈 줄) 단위로 분할(1덩어리 금지).
    md = "첫 단락 내용\n\n둘째 단락 내용\n\n셋째 단락"
    recs = _segment_markdown(md, [(0, 1)])
    assert len(recs) == 3
    assert recs[0]["text"] == "첫 단락 내용"
    assert recs[2]["text"] == "셋째 단락"
    assert [r["index"] for r in recs] == [0, 1, 2]


def test_paragraph_blocks_tagged_with_page() -> None:
    # 2페이지 page_map: 0~9=page1, 10~=page2. 둘째 단락은 offset>=10 → page2.
    md = "짧은1\n\n" + ("x" * 8) + "\n\n둘째페이지단락"
    page_map = [(0, 1), (14, 2)]
    recs = _segment_markdown(md, page_map)
    assert recs[0]["page"] == 1
    assert recs[-1]["page"] == 2


def test_page_for_offset_picks_last_start_le_offset() -> None:
    pm = [(0, 1), (100, 2), (200, 3)]
    assert _page_for_offset(pm, 0) == 1
    assert _page_for_offset(pm, 150) == 2
    assert _page_for_offset(pm, 999) == 3
    assert _page_for_offset([], 5) is None
