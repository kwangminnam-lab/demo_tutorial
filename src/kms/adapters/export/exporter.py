"""답변/요약을 PDF·DOCX·TXT 파일 바이트로 내보내는 어댑터 (ADR-008).

side effect(렌더링 라이브러리 호출)는 이 경계에 격리한다. domain/services는
`export()`만 부른다. PDF 한글은 유니코드 TTF 폰트를 등록해 처리하고, 폰트를
못 찾으면 (비-latin 텍스트일 때) `ExportError`로 명확히 실패한다.
"""

import os
from io import BytesIO

from docx import Document as DocxDocument
from fpdf import FPDF

from kms.adapters.export.base import ExportError, ExportFormat, render_answer
from kms.domain.models import Answer

# 유니코드(한글 포함) TTF 후보. 환경 변수 `KMS_PDF_FONT_PATH`가 최우선이고,
# 없으면 OS별 흔한 시스템 폰트를 순서대로 찾는다. CJK 글리프가 있는 폰트만 둔다.
_FONT_CANDIDATES: tuple[str, ...] = (
    # macOS (AppleGothic.ttf는 OS/2 테이블이 없어 fpdf2가 못 읽으므로 제외)
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    # Linux (Noto/Nanum)
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    # Windows
    "C:/Windows/Fonts/malgun.ttf",
)


def _font_loads(path: str) -> bool:
    """fpdf2가 실제로 등록할 수 있는 폰트인지 시험 등록으로 확인한다.

    일부 시스템 TTF는 필수 테이블(OS/2 등)이 없어 fontTools가 KeyError/TTLibError
    등을 던진다. 어떤 예외든 "사용 불가"로 보고 다음 후보로 넘어간다(광범위 catch
    의도적 — 깨진 폰트를 조용히 건너뛰되 PDF 생성 자체는 막지 않기 위함).
    """
    try:
        FPDF().add_font("probe", "", path)
    except Exception:
        return False
    return True


def _resolve_font() -> str | None:
    """등록 가능한 유니코드 폰트 경로를 찾는다. 없으면 None."""
    override = os.environ.get("KMS_PDF_FONT_PATH")
    if override and os.path.isfile(override) and _font_loads(override):
        return override
    for candidate in _FONT_CANDIDATES:
        if os.path.isfile(candidate) and _font_loads(candidate):
            return candidate
    return None


def _is_latin1(text: str) -> bool:
    """fpdf2 코어 폰트(Helvetica)로 표현 가능한 latin-1 전용 텍스트인가."""
    try:
        text.encode("latin-1")
    except UnicodeEncodeError:
        return False
    return True


def _to_txt(text: str) -> bytes:
    return text.encode("utf-8")


def _to_docx(text: str) -> bytes:
    document = DocxDocument()
    for line in text.split("\n"):
        document.add_paragraph(line)
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _to_pdf(text: str) -> bytes:
    pdf = FPDF()
    pdf.add_page()

    font_path = _resolve_font()
    if font_path is not None:
        pdf.add_font("uni", "", font_path)
        pdf.set_font("uni", size=11)
    elif _is_latin1(text):
        # 한글 등 비-latin 문자가 없으면 코어 폰트로 충분하다.
        pdf.set_font("Helvetica", size=11)
    else:
        raise ExportError(
            "PDF 내보내기에 유니코드 폰트가 필요합니다. "
            "환경 변수 KMS_PDF_FONT_PATH에 한글 지원 TTF 경로를 지정하세요."
        )

    for line in text.split("\n"):
        # 너비는 유효 페이지 폭(epw)으로 명시하고, 줄마다 좌마진으로 복귀+다음 줄로
        # 내려가게 한다(너비 0은 커서 위치에 따라 폭이 음수가 될 수 있어 회피).
        # 빈 줄은 multi_cell이 거부하므로 공백 한 칸으로 대체한다.
        pdf.multi_cell(pdf.epw, 8, line if line else " ", new_x="LMARGIN", new_y="NEXT")

    return bytes(pdf.output())


def export(content: str | Answer, fmt: ExportFormat) -> bytes:
    """`content`(평문 또는 `Answer`)를 `fmt` 형식 파일 바이트로 내보낸다.

    `Answer`는 본문 + 출처 인용으로 렌더링한다(출처 누락 금지 — ADR-007).
    """
    text = render_answer(content) if isinstance(content, Answer) else content

    if fmt is ExportFormat.TXT:
        return _to_txt(text)
    if fmt is ExportFormat.DOCX:
        return _to_docx(text)
    if fmt is ExportFormat.PDF:
        return _to_pdf(text)

    raise ExportError(f"지원하지 않는 내보내기 형식: {fmt}")
