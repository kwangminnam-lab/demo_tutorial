"""문서 비교 라우트 (POST /v1/diff) — 권한 인지 텍스트 diff의 HTTP 경계.

진입점은 얇게(CONVENTIONS·ADR-008): 본문 파싱 → 인증 → doc_id를 (경로, 메타)로
해소 → `DiffService.diff_documents`에 위임 → `DiffResult` JSON 매핑만 한다. 추출·
라인/단어 diff·권한 검사 등 비즈니스 로직은 전부 `DiffService`에 있다.

권한 강제: `diff_documents`가 두 문서 모두 가시일 때만 비교하고, 한 쪽이라도
권한 밖이면 `AuthorizationError`를 던진다 — 여기서 403으로 변환한다(조용한 실패
금지). 미등록 doc_id는 해소기가 `KeyError`를 던지고 404로 변환된다.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from kms.api.deps import (
    DocumentResolver,
    get_current_user,
    get_diff_service,
    get_document_resolver,
)
from kms.domain.errors import AuthorizationError
from kms.domain.models import DiffResult, UserContext
from kms.services._page_render import render_page_previews
from kms.services.diff_service import (
    DiffService,
    _collect_highlights,
    _extract_with_blobs,
)

router = APIRouter(prefix="/v1", tags=["diff"])


class DiffRequest(BaseModel):
    """비교할 두 문서의 doc_id."""

    doc_id_a: str = Field(min_length=1)
    doc_id_b: str = Field(min_length=1)


@router.post("/diff", response_model=DiffResult)
def diff(
    request: DiffRequest,
    user: UserContext = Depends(get_current_user),
    service: DiffService = Depends(get_diff_service),
    resolver: DocumentResolver = Depends(get_document_resolver),
) -> DiffResult:
    """두 문서를 비교한다. 한 쪽이라도 권한 밖이면 403, 미등록 doc_id면 404."""
    try:
        path_a, meta_a = resolver(request.doc_id_a)
        path_b, meta_b = resolver(request.doc_id_b)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="문서를 찾을 수 없습니다"
        ) from exc
    try:
        return service.diff_documents(path_a, path_b, meta_a, meta_b, user)
    except AuthorizationError as exc:
        # 권한 밖 문서 비교 시도 — 인증은 됐으므로 401이 아닌 403으로 구분한다.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc


# 업로드 한 파일당 상한 — 메모리·디스크 보호. 큰 PDF/PPT를 감안해 넉넉히 잡되 무한대는 X.
_MAX_UPLOAD_BYTES = 80 * 1024 * 1024  # 80 MiB


@router.post("/diff/upload", response_model=DiffResult)
async def diff_upload(
    file_a: UploadFile = File(...),
    file_b: UploadFile = File(...),
    user: UserContext = Depends(get_current_user),
    service: DiffService = Depends(get_diff_service),
) -> DiffResult:
    """**업로드한 두 파일**을 비교한다 — 서버 인덱스에 등록되지 않은 자료 비교 경로.

    저장된 자료 비교(`/v1/diff`)와 분리한다: 업로드 바이트는 어떤 인덱스/저장소에도
    영속되지 않고 임시 파일로만 추출에 쓰인 뒤 즉시 삭제된다(권한 매트릭스 밖).
    인증된 사용자가 본인이 올린 파일만 비교하므로 access 매트릭스 강제는 적용하지
    않는다(자기 자료 자기 비교). 권한 체크는 향후 다중 사용자 협업 업로드 시 도입.

    제한: 파일당 80 MiB(`_MAX_UPLOAD_BYTES`). 초과 시 413 Payload Too Large.
    """
    user  # noqa: B018 — 인증 의존만 강제(미인증 401), 사용자 컨텍스트 자체는 본 경로에서 미사용.
    suffix_a = _safe_suffix(file_a.filename)
    suffix_b = _safe_suffix(file_b.filename)
    bytes_a = await _read_bounded(file_a)
    bytes_b = await _read_bounded(file_b)
    # 1) 추출 — 텍스트 + 이미지 blobs (페이지 프리뷰는 후속 단계에서 highlight와 함께)
    text_a, blobs_a, tmp_a = _extract_to_tmp(bytes_a, suffix_a)
    text_b, blobs_b, tmp_b = _extract_to_tmp(bytes_b, suffix_b)
    try:
        # 2) text diff → 변경 텍스트 수집
        result = service.diff_texts(text_a, text_b)
        result.image_blobs = {**blobs_a, **blobs_b}
        left_hl, right_hl = _collect_highlights(result)
        # 3) 페이지 프리뷰 + 색칠
        result.page_previews_a = render_page_previews(
            tmp_a, highlights=list(left_hl) if left_hl else None
        )
        result.page_previews_b = render_page_previews(
            tmp_b, highlights=list(right_hl) if right_hl else None
        )
        return result
    finally:
        # 추출·diff·렌더 성공/실패와 무관하게 임시 파일 항상 삭제(시크릿 잔존 방지).
        tmp_a.unlink(missing_ok=True)
        tmp_b.unlink(missing_ok=True)


def _safe_suffix(name: str | None) -> str:
    """원본 파일명에서 확장자만 보존(소문자) — 추출기 선택에 필요. 없으면 빈 문자열."""
    if not name:
        return ""
    suffix = Path(name).suffix.lower()
    # 알파벳·숫자·점만 허용 — 임시 경로 주입 차단.
    return suffix if all(c.isalnum() or c == "." for c in suffix) else ""


async def _read_bounded(upload: UploadFile) -> bytes:
    """업로드 바이트를 한도 내에서만 읽는다. 초과 시 413."""
    data = await upload.read(_MAX_UPLOAD_BYTES + 1)
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"파일이 너무 큽니다(상한 {_MAX_UPLOAD_BYTES // (1024 * 1024)} MiB).",
        )
    return data


def _extract_to_tmp(
    data: bytes, suffix: str
) -> tuple[str, dict[str, str], Path]:
    """바이트 → 임시 파일 → (평문, image_blobs, tmp_path). 호출자가 unlink 책임.

    페이지 프리뷰는 호출자가 변경 텍스트(diff 결과)와 함께 한 번에 렌더하므로
    여기서는 추출만 하고 tmp_path를 반환한다. 추출 실패 시에도 tmp_path는 정리한다.
    """
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    try:
        text, blobs = _extract_with_blobs(tmp_path)
    except Exception:
        # 추출 실패 시 임시 파일 즉시 정리 후 예외 전파.
        tmp_path.unlink(missing_ok=True)
        raise
    return text, blobs, tmp_path
