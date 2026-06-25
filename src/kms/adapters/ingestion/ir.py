"""추출 중간표현(IR) — 추출기와 청킹의 경계 계약.

추출기는 바이너리(PDF/DOCX/PPTX/XLSX/TXT)를 파싱해 여기 정의된 IR로 반환하고,
다음 step의 청킹 함수는 바이너리 파싱 없이 이 IR만 받는다. 이렇게 분리해야
청킹을 작은 IR 객체로 결정론적으로 테스트할 수 있다 (외부 라이브러리·파일 불필요).

형식별 IR:
- `MarkdownDoc`: 헤더(`#/##/###`)를 포함한 마크다운 + 페이지 경계 — PDF/DOCX/TXT.
- `SlideDeck`: 슬라이드별 제목·본문·스피커노트 — PPTX.
- `Workbook`: 시트별 표(컬럼·행) — XLSX.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DocBlock:
    """문서 한 블록(문단·표·제목 등) + 페이지 위치.

    파싱 미리보기에서 "코드 hover → 원본의 실제 문단/표 위치 강조"에 쓴다.
    `bbox`는 **페이지 크기 대비 정규화된 top-left 좌표** (x, y, w, h ∈ [0,1])로,
    프런트가 페이지 프리뷰 이미지 위에 그대로 % 오버레이할 수 있다. 좌표를 못 구하면
    None(페이지 단위 강조로 폴백).
    """

    page: int
    text: str
    kind: str  # text | table | section_header | list_item | caption | ...
    bbox: tuple[float, float, float, float] | None = None


@dataclass
class MarkdownDoc:
    """마크다운 본문 + 페이지 맵.

    `page_map`은 (문자 오프셋, 페이지 번호) 쌍의 목록으로, 각 페이지가
    `markdown`의 어느 오프셋에서 시작하는지 기록한다. 페이지 개념이 없는
    형식(DOCX/TXT)에서는 비운다.

    `image_blobs`는 본문에 박힌 `[IMAGE sha=...]` 마커가 가리키는 실제 이미지
    바이트(sha8 → blob)다. 문서 비교 UI가 라벨 대신 실제 그림을 렌더할 때 쓴다.

    `blocks`는 문단/표 단위 + 페이지 내 정규화 bbox다(가용 시). 파싱 미리보기의
    정밀 위치 강조에 쓰며, 추출기가 좌표를 못 주면 빈 리스트.

    `html`은 파서가 충실도 높은 HTML(표·구조 보존)을 줄 수 있을 때의 렌더 결과다
    (docling export_to_html 등). 비어 있으면 markdown→HTML 폴백으로 렌더한다.
    """

    markdown: str
    page_map: list[tuple[int, int]] = field(default_factory=list)
    image_blobs: dict[str, str] = field(default_factory=dict)
    blocks: list[DocBlock] = field(default_factory=list)
    html: str = ""


@dataclass
class Slide:
    """PPTX 슬라이드 한 장 — 제목·본문·스피커노트."""

    number: int
    title: str | None
    body: str
    notes: str | None


@dataclass
class SlideDeck:
    """PPTX 전체 — 슬라이드 목록 + 이미지 blob 맵."""

    slides: list[Slide] = field(default_factory=list)
    image_blobs: dict[str, str] = field(default_factory=dict)


@dataclass
class Table:
    """XLSX 시트 한 장 — 표 제목·컬럼·행."""

    title: str
    columns: list[str]
    rows: list[dict[str, Any]]


@dataclass
class Workbook:
    """XLSX 전체 — 표 목록."""

    tables: list[Table] = field(default_factory=list)
    image_blobs: dict[str, str] = field(default_factory=dict)


#: 추출기가 반환할 수 있는 IR 타입.
IR = MarkdownDoc | SlideDeck | Workbook
