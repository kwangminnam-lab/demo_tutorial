"""structure.py 단위 테스트 — 헤더 계층 추출 + markdown_to_text 정제.

순수 변환 로직만 검증한다(I/O·네트워크 없음). 결정론: 같은 입력 → 같은 출력.
"""

from kms.adapters.ingestion.structure import (
    Section,
    build_sections,
    markdown_to_text,
)


def test_build_sections_multilevel_headings_capture_level_title_path() -> None:
    markdown = (
        "# 1장\n"
        "intro\n"
        "## 1.1절\n"
        "본문 A\n"
        "### 1.1.1항\n"
        "본문 B\n"
        "# 2장\n"
        "본문 C\n"
    )
    sections = build_sections(markdown, [])

    assert [(s.level, s.title) for s in sections] == [
        (1, "1장"),
        (2, "1.1절"),
        (3, "1.1.1항"),
        (1, "2장"),
    ]
    assert sections[0].path == ["1장"]
    assert sections[1].path == ["1장", "1.1절"]
    assert sections[2].path == ["1장", "1.1절", "1.1.1항"]
    # 2장은 level 1로 되돌아가 상위 경로가 리셋된다.
    assert sections[3].path == ["2장"]
    assert sections[1].text == "본문 A"


def test_build_sections_plaintext_returns_single_section() -> None:
    sections = build_sections("헤더 없는 평문\n둘째 줄", [])

    assert len(sections) == 1
    section = sections[0]
    assert section.level == 0
    assert section.title == "문서"
    assert "헤더 없는 평문" in section.text
    assert "둘째 줄" in section.text


def test_build_sections_empty_input_returns_single_section_not_empty() -> None:
    sections = build_sections("", [])

    assert sections == [Section(level=0, title="문서", text="", page=None, path=[])]


def test_build_sections_preamble_before_first_heading_preserved() -> None:
    markdown = "머리말 문장\n# 1장\n본문"
    sections = build_sections(markdown, [])

    assert len(sections) == 2
    assert sections[0].level == 0
    assert sections[0].text == "머리말 문장"
    assert sections[1].title == "1장"


def test_build_sections_page_resolved_from_page_map() -> None:
    # "# A\n본문a\n" = 8글자 → "# B" 헤더가 offset 8에서 시작. 페이지 경계 8을 2페이지로.
    markdown = "# A\n본문a\n# B\n본문b"
    page_map = [(0, 1), (8, 2)]
    sections = build_sections(markdown, page_map)

    assert sections[0].page == 1
    assert sections[1].page == 2


def test_build_sections_empty_page_map_yields_none_page() -> None:
    sections = build_sections("# A\n본문", [])

    assert sections[0].page is None


def test_build_sections_is_deterministic() -> None:
    markdown = "# A\n본문\n## B\n본문2"
    assert build_sections(markdown, []) == build_sections(markdown, [])


def test_markdown_to_text_strips_heading_symbols() -> None:
    assert markdown_to_text("# 제목\n## 부제목") == "제목\n부제목"


def test_markdown_to_text_strips_image_markers() -> None:
    text = markdown_to_text("앞 [IMAGE p=1 sha=abc12345 x2] 뒤")
    assert "IMAGE" not in text
    assert "sha" not in text
    assert "앞" in text and "뒤" in text


def test_markdown_to_text_strips_emphasis_and_links() -> None:
    out = markdown_to_text("**굵게** 와 *기울임* 과 [링크](http://x) 와 `코드`")
    assert "*" not in out
    assert "`" not in out
    assert "http://x" not in out
    assert "굵게" in out
    assert "기울임" in out
    assert "링크" in out
    assert "코드" in out


def test_markdown_to_text_strips_list_and_blockquote_markers() -> None:
    out = markdown_to_text("- 항목1\n- 항목2\n> 인용문\n1. 첫째")
    assert out == "항목1\n항목2\n인용문\n첫째"
