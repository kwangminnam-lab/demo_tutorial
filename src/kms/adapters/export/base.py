"""내보내기 형식 + 답변 렌더링 (외부 라이브러리 없는 순수 부분)."""

from enum import Enum

from kms.domain.models import Answer, Citation


class ExportFormat(str, Enum):
    """요약/답변 내보내기 형식 (ADR-008 PDF/DOCX/TXT)."""

    PDF = "pdf"
    DOCX = "docx"
    TXT = "txt"


class ExportError(Exception):
    """내보내기 실패 — 시스템/구성 에러(예: PDF 유니코드 폰트 없음).

    조용히 삼키지 않고 명확한 사유로 전파한다 (CONVENTIONS 조용한 실패 금지).
    """


def _citation_location(citation: Citation) -> str:
    """출처의 페이지/슬라이드 위치를 사람이 읽는 문자열로. 없으면 빈 문자열."""
    if citation.page is not None:
        return f" p.{citation.page}"
    if citation.slide_no is not None:
        return f" slide {citation.slide_no}"
    return ""


def render_answer(answer: Answer) -> str:
    """답변 본문 + 출처 인용 목록을 평문/마크다운으로 구성한다.

    출처 인용은 내보내기에서 절대 누락하지 않는다 — 근거 추적성(ADR-007).
    근거가 0건(`grounded=False`)이면 본문만 반환한다.
    """
    lines = [answer.text]
    if answer.citations:
        lines.append("")
        lines.append("출처:")
        for index, citation in enumerate(answer.citations, start=1):
            location = _citation_location(citation)
            lines.append(
                f"[{index}] {citation.source.value} {citation.doc_id}"
                f"{location}: {citation.snippet}"
            )
    return "\n".join(lines)
