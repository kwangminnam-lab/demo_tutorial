"""문서 구조 추출 — MarkdownDoc의 마크다운을 명시적 섹션 트리로 환원.

IR(`MarkdownDoc.markdown`)의 헤더 계층(`#`/`##`/...)은 암묵적이다. 이 모듈은 그
계층을 `Section` 목록으로 명시화하고, 본문을 가독 plain text로 정제하는 순수
변환 함수를 제공한다. 외부 I/O·네트워크·DB 없음 — 같은 입력 → 같은 출력(결정론).

직렬화·파일쓰기는 이 모듈의 책임이 아니다(step 4/5). 여기서는 변환만 한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: 단일(헤더 없음)·프리앰블 섹션의 기본 제목 — 파일명이 이 레이어로 전달되지 않으므로 일반 라벨.
_DEFAULT_TITLE = "문서"

# image 마커: [IMAGE p=N sha=abc12345 xN] / [IMAGE sha=abc xN] / [IMAGE sha=unknown]
# parse.py의 `_IMAGE_MARKER_RE`와 동일 패턴(레이어 분리상 복제 — 의존 방향을 api로 거꾸로 두지 않음).
_IMAGE_MARKER_RE = re.compile(
    r"\[IMAGE(?:\s+p=(?P<page>\d+))?(?:\s+#\d+)?\s+sha=(?P<sha>[0-9a-f]+|unknown)(?:\s+x\d+)?\]"
)

#: 헤더 줄: 선행 공백 0~3, `#`×1~6, 공백, 제목.
_HEADING_LINE_RE = re.compile(r"^[ \t]{0,3}(#{1,6})[ \t]+(.*?)[ \t]*$")

# markdown_to_text용 줄 단위 정제 패턴.
_HEADING_PREFIX_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+")
_BLOCKQUOTE_RE = re.compile(r"^[ \t]{0,3}>[ \t]?")
_LIST_MARKER_RE = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])[ \t]+")
#: `![alt](url)` / `[text](url)` → 텍스트(alt/text)만 남김.
_LINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
#: `**bold**` / `__bold__` → 내용만.
_BOLD_RE = re.compile(r"(\*\*|__)(.+?)\1")
#: `*italic*` / `_italic_` → 내용만.
_ITALIC_RE = re.compile(r"(?<!\w)([*_])(.+?)\1(?!\w)")
_MULTI_BLANK_RE = re.compile(r"\n{3,}")


@dataclass(frozen=True)
class Section:
    """문서 한 섹션 — 헤더 경계로 분할된 구조 단위(파싱 산출물 표현, adapters 레이어).

    - `level`: 헤더 깊이(1..6). 헤더 없는 단일/프리앰블 섹션은 0.
    - `title`: 헤더 제목. 헤더 없는 섹션은 `_DEFAULT_TITLE`.
    - `text`: 해당 섹션 본문 plain text(`markdown_to_text`로 정제).
    - `page`: 섹션 시작 오프셋이 속한 페이지(page_map으로 해석). 없으면 None.
    - `path`: 상위→현재 헤더 제목 누적 경로(예: ["1장", "1.1절"]). 프리앰블은 [].
    """

    level: int
    title: str
    text: str
    page: int | None = None
    path: list[str] = field(default_factory=list)


def markdown_to_text(markdown: str) -> str:
    """마크다운을 가독 plain text로 정제.

    헤더 기호·리스트 마커·blockquote·강조(`**`/`*`/`_`)·인라인 코드(`` ` ``)·
    링크/이미지 문법·`[IMAGE ...]` 마커를 제거한다. 줄 구조는 보존하되 3줄 이상
    연속 공백 줄은 2줄로 접는다.
    """
    text = _IMAGE_MARKER_RE.sub("", markdown)
    cleaned: list[str] = []
    for raw in text.split("\n"):
        line = _HEADING_PREFIX_RE.sub("", raw)
        line = _BLOCKQUOTE_RE.sub("", line)
        line = _LIST_MARKER_RE.sub("", line)
        line = _LINK_RE.sub(r"\1", line)
        line = _BOLD_RE.sub(r"\2", line)
        line = _ITALIC_RE.sub(r"\2", line)
        line = line.replace("`", "")
        cleaned.append(line.rstrip())
    joined = "\n".join(cleaned)
    return _MULTI_BLANK_RE.sub("\n\n", joined).strip()


def build_sections(
    markdown: str, page_map: list[tuple[int, int]]
) -> list[Section]:
    """마크다운을 헤더 경계로 분할해 `Section` 목록으로 환원한다.

    헤더(`#`~`######`)를 만날 때마다 새 섹션을 연다. 각 섹션 `text`는 그 헤더
    다음부터 다음 헤더 전까지의 본문을 `markdown_to_text`로 정제한 것이다.
    `page`는 `page_map`(문자 오프셋→페이지)으로 섹션 시작 오프셋을 해석한다.

    헤더가 하나도 없으면 전체를 단일 `Section(level=0)`으로 반환한다(빈 결과 금지).
    첫 헤더 이전에 본문이 있으면 프리앰블도 `level=0` 섹션으로 보존한다(내용 누락 금지).
    """
    sections: list[Section] = []
    stack: list[tuple[int, str]] = []  # (level, title) — 상위 헤더 경로
    current: dict[str, object] | None = None
    preamble: list[str] = []
    first_heading_seen = False
    offset = 0

    for line in markdown.split("\n"):
        match = _HEADING_LINE_RE.match(line)
        if match:
            if not first_heading_seen:
                first_heading_seen = True
                _emit_preamble(sections, preamble, page_map)
            if current is not None:
                sections.append(_finalize(current, page_map))
            level = len(match.group(1))
            title = match.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            current = {
                "start": offset,
                "level": level,
                "title": title,
                "path": [t for _, t in stack],
                "body": [],
            }
        elif current is not None:
            body = current["body"]
            assert isinstance(body, list)
            body.append(line)
        else:
            preamble.append(line)
        offset += len(line) + 1  # +1: split으로 사라진 개행 보정

    if current is not None:
        sections.append(_finalize(current, page_map))

    if not sections:
        # 헤더가 전혀 없거나 모두 빈 본문 — 전체를 단일 섹션으로(빈 리스트 금지).
        sections.append(
            Section(
                level=0,
                title=_DEFAULT_TITLE,
                text=markdown_to_text(markdown),
                page=_page_for_offset(page_map, 0),
                path=[],
            )
        )
    return sections


def _emit_preamble(
    sections: list[Section],
    preamble_lines: list[str],
    page_map: list[tuple[int, int]],
) -> None:
    """첫 헤더 이전 본문이 비어있지 않으면 level=0 섹션으로 추가한다."""
    if not any(line.strip() for line in preamble_lines):
        return
    text = markdown_to_text("\n".join(preamble_lines))
    if not text:
        return
    sections.append(
        Section(
            level=0,
            title=_DEFAULT_TITLE,
            text=text,
            page=_page_for_offset(page_map, 0),
            path=[],
        )
    )


def _finalize(
    section: dict[str, object], page_map: list[tuple[int, int]]
) -> Section:
    """수집한 섹션 dict를 `Section`으로 확정(본문 정제 + 페이지 해석)."""
    body = section["body"]
    assert isinstance(body, list)
    level = section["level"]
    title = section["title"]
    path = section["path"]
    start = section["start"]
    assert isinstance(level, int)
    assert isinstance(title, str)
    assert isinstance(path, list)
    assert isinstance(start, int)
    return Section(
        level=level,
        title=title,
        text=markdown_to_text("\n".join(body)),
        page=_page_for_offset(page_map, start),
        path=path,
    )


def _page_for_offset(
    page_map: list[tuple[int, int]], offset: int
) -> int | None:
    """문자 오프셋이 속한 페이지를 page_map으로 해석한다(없으면 None).

    오프셋 이하의 마지막 페이지 경계를 그 페이지로 본다(markdown_chunker와 동일 규칙).
    """
    if not page_map:
        return None
    page = page_map[0][1]
    for boundary, page_no in page_map:
        if boundary <= offset:
            page = page_no
        else:
            break
    return page
