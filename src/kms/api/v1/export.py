"""내보내기 라우트 (POST /v1/export) — 답변/평문을 파일 바이트로 다운로드.

진입점은 얇게(CONVENTIONS·ADR-008): 인증 → 본문 검증 → `export()`에 위임 →
파일 바이트를 적절한 MIME·`Content-Disposition`으로 반환만 한다. 렌더링(PDF/DOCX/
TXT, 출처 인용 포함)은 전부 `adapters/export`에 있다.

권한: `get_current_user`로 인증을 강제한다(미인증 401). 내보낼 본문은 호출자가
바디로 넘긴 `Answer`/평문이므로 별도 문서 권한 검사는 없다 — 권한 필터는 그
`Answer`를 만든 RAG 단계에서 이미 적용됐다. 렌더링 실패(예: PDF 유니코드 폰트
없음, 미지원 형식)는 `ExportError`를 422로 변환한다(조용한 실패 금지).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel, model_validator

from kms.adapters.export import ExportError, ExportFormat, export
from kms.api.deps import get_current_user
from kms.domain.models import Answer, UserContext

router = APIRouter(prefix="/v1", tags=["export"])

# 형식별 MIME 타입과 다운로드 파일명 확장자.
_MEDIA_TYPES: dict[ExportFormat, str] = {
    ExportFormat.PDF: "application/pdf",
    ExportFormat.DOCX: (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ),
    ExportFormat.TXT: "text/plain; charset=utf-8",
}


class ExportRequest(BaseModel):
    """내보낼 내용 — `answer`(출처 인용 포함) 또는 `content`(평문) 중 정확히 하나."""

    content: str | None = None
    answer: Answer | None = None
    format: ExportFormat

    @model_validator(mode="after")
    def _exactly_one_source(self) -> "ExportRequest":
        if (self.content is None) == (self.answer is None):
            raise ValueError("content 또는 answer 중 정확히 하나를 제공하세요.")
        return self


@router.post("/export")
def export_document(
    request: ExportRequest,
    _user: UserContext = Depends(get_current_user),
) -> Response:
    """요청 형식(PDF/DOCX/TXT)의 파일 바이트를 다운로드로 반환한다."""
    payload: str | Answer = (
        request.answer if request.answer is not None else (request.content or "")
    )
    try:
        data = export(payload, request.format)
    except ExportError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    filename = f"export.{request.format.value}"
    return Response(
        content=data,
        media_type=_MEDIA_TYPES[request.format],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
