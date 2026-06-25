"""필드추출(IDP) 라우트 (ADR-024 phase 1).

- 스키마 관리(목록/생성/삭제/자동제안)는 마스터 전용(`require_master`) — 추출 규칙은
  관리 행위.
- 추출 실행·결과 조회는 인증 사용자(`get_current_user`).

업로드 파일은 임시 파일로만 추출·렌더에 쓴 뒤 즉시 삭제한다(parse 라우트와 동일 —
디스크 잔존 방지). `doc_id`는 파일 내용 해시(멱등 — 같은 파일=같은 결과 키).
"""

from __future__ import annotations

import hashlib
import logging
import tempfile
from pathlib import Path

from collections.abc import Callable

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field

from kms.adapters.ingestion.image_pdf import PAGE_DOC_EXTS
from kms.adapters.ingestion.jsonl import to_extract_jsonl
from kms.adapters.storage import write_export
from kms.api.deps import get_current_user, get_extraction_service, require_master
from kms.api.v1._download import jsonl_response
from kms.config.settings import get_settings
from kms.domain.extraction import ExtractionResult, ExtractionSchema
from kms.domain.models import UserContext
from kms.services.extraction_service import ExtractionService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/extract", tags=["extract"])

_MAX_UPLOAD_BYTES = 80 * 1024 * 1024  # 80 MiB (parse/diff와 동일)
# 디지털 PDF + 스캔 이미지(PNG/JPG 등). 이미지는 VLM(Gemini) 경로로 추출(ADR-025).
_SUPPORTED_EXTS = PAGE_DOC_EXTS


class ExtractResponse(BaseModel):
    """추출 응답 — 결과 + 페이지별 근거(B-Box) PNG 미리보기."""

    result: ExtractionResult
    evidence_previews: dict[int, str] = Field(
        default_factory=dict,
        description="페이지 번호(1-base) → 근거 사각형이 칠해진 PNG data URL.",
    )
    export_path: str | None = Field(
        default=None,
        description="공유 PVC(export_root)에 영속된 JSONL의 상대경로(예: extract/<doc_id>.jsonl). 실패 시 None.",
    )
    export_error: str | None = Field(
        default=None,
        description="export 실패 사유(조용한 실패 금지). 핵심 추출 결과는 그대로 반환된다.",
    )


# ── 스키마 관리 (마스터 전용) ─────────────────────────────────────────────
@router.get("/schemas", response_model=list[ExtractionSchema])
def list_schemas(
    _user: UserContext = Depends(get_current_user),
    service: ExtractionService = Depends(get_extraction_service),
) -> list[ExtractionSchema]:
    """등록된 추출 스키마 목록."""
    return service.list_schemas()


@router.post("/schemas", response_model=ExtractionSchema, status_code=status.HTTP_201_CREATED)
def create_schema(
    schema: ExtractionSchema,
    _admin: UserContext = Depends(require_master),
    service: ExtractionService = Depends(get_extraction_service),
) -> ExtractionSchema:
    """추출 스키마를 생성한다(마스터)."""
    return service.create_schema(schema)


@router.put("/schemas/{schema_id}", response_model=ExtractionSchema)
def update_schema(
    schema_id: int,
    schema: ExtractionSchema,
    _admin: UserContext = Depends(require_master),
    service: ExtractionService = Depends(get_extraction_service),
) -> ExtractionSchema:
    """기존 추출 스키마를 수정한다(마스터). 없으면 404. 본문 id 는 무시(경로값 사용)."""
    updated = service.update_schema(schema_id, schema)
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="스키마를 찾을 수 없습니다"
        )
    return updated


@router.delete("/schemas/{schema_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_schema(
    schema_id: int,
    _admin: UserContext = Depends(require_master),
    service: ExtractionService = Depends(get_extraction_service),
) -> None:
    """추출 스키마를 삭제한다(마스터). 없으면 404."""
    if not service.delete_schema(schema_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="스키마를 찾을 수 없습니다"
        )


@router.post("/schemas/auto", response_model=ExtractionSchema)
async def auto_schema(
    file: UploadFile = File(...),
    doc_type: str | None = Form(default=None),
    name: str | None = Form(default=None),
    vlm: bool = Form(default=False),
    _admin: UserContext = Depends(require_master),
    service: ExtractionService = Depends(get_extraction_service),
) -> ExtractionSchema:
    """업로드 문서에서 추출 스키마를 자동 제안한다(마스터, 미영속 — 승인 후 저장).

    vlm=True면 Gemini 비전으로 손글씨/스캔 이미지에서 제안한다(외부 전송).
    """
    path, cleanup = await _save_upload(file)
    try:
        try:
            return service.propose_schema(
                path, doc_type=doc_type, name=name, prefer_vlm=vlm
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
    finally:
        cleanup()


# ── 추출 실행 + 결과 조회 (인증 사용자) ───────────────────────────────────
@router.post("/run", response_model=ExtractResponse)
async def run_extraction(
    file: UploadFile = File(...),
    schema_id: int = Form(...),
    vlm: bool = Form(default=False),
    user: UserContext = Depends(get_current_user),
    service: ExtractionService = Depends(get_extraction_service),
) -> ExtractResponse:
    """업로드 문서를 지정 스키마(schema_id)로 추출하고 근거 미리보기와 함께 반환한다.

    vlm=True면 손글씨/스캔 모드(Gemini 비전, 외부 전송). 미구성이면 422.
    """
    result, path, cleanup = await _run(file, schema_id, vlm, user, service)
    try:
        previews = service.render_evidence(path, result)
    finally:
        cleanup()
    # 서버측 영속(F5): 추출 결과를 JSONL로 export_root/extract/<doc_id>.jsonl에 쓴다.
    # 다운로드(/run.jsonl)와 별개 — export 실패는 추출 결과 응답을 막지 않는다(graceful).
    export_path, export_error = _persist_extract_jsonl(result)
    return ExtractResponse(
        result=result,
        evidence_previews=previews,
        export_path=export_path,
        export_error=export_error,
    )


@router.post("/run.jsonl")
async def run_extraction_jsonl(
    file: UploadFile = File(...),
    schema_id: int = Form(...),
    vlm: bool = Form(default=False),
    user: UserContext = Depends(get_current_user),
    service: ExtractionService = Depends(get_extraction_service),
) -> Response:
    """추출을 실행해 결과를 JSONL(필드당 1줄)로 다운로드한다(application/x-ndjson).

    `/run`(결과+근거 미리보기 JSON)과 별개 — 같은 추출 결과를 줄단위로 직렬화해 첨부로
    내려준다. 근거 렌더는 생략(다운로드엔 불필요).
    """
    result, _path, cleanup = await _run(file, schema_id, vlm, user, service)
    cleanup()
    filename = file.filename or result.doc_id
    return jsonl_response(to_extract_jsonl(result), filename)


class ExportResult(BaseModel):
    """수정본 영속 응답 — export_root에 쓰인 상대경로 또는 실패 사유."""

    export_path: str | None = None
    export_error: str | None = None


@router.post("/results/export", response_model=ExportResult)
def export_corrected_result(
    result: ExtractionResult,
    _user: UserContext = Depends(get_current_user),
) -> ExportResult:
    """사용자가 보정한 추출 결과(값 수정)를 export_root/extract/<doc_id>.jsonl에 영속한다.

    AI OCR 결과의 잘못된 값을 화면에서 고친 뒤 저장하는 경로 — 추출을 재실행하지 않고
    전달된 결과를 그대로 JSONL로 덮어쓴다(과금·비결정 회피). export 실패는 조용히
    삼키지 않고 export_error로 노출한다(핵심 흐름은 graceful).
    """
    export_path, export_error = _persist_extract_jsonl(result)
    return ExportResult(export_path=export_path, export_error=export_error)


def _persist_extract_jsonl(result: ExtractionResult) -> tuple[str | None, str | None]:
    """추출 JSONL을 export_root/extract/<doc_id>.jsonl에 영속한다(공유 PVC, F5).

    (상대경로, 에러) 튜플을 반환한다 — 성공이면 (상대경로, None), 실패면 (None, 사유).
    export 실패(PVC 미마운트·권한 등)는 추출 결과 응답을 막지 않되 조용히 삼키지 않는다
    (warning 로깅 + 응답 export_error 노출).
    """
    out_name = f"{result.doc_id}.jsonl"
    try:
        write_export("extract", out_name, to_extract_jsonl(result), get_settings().export_root)
    except OSError as exc:
        logger.warning("추출 JSONL export 실패 (doc_id=%s): %s", result.doc_id, exc)
        return None, f"export 실패: {type(exc).__name__}"
    return f"extract/{out_name}", None


async def _run(
    file: UploadFile,
    schema_id: int,
    vlm: bool,
    user: UserContext,
    service: ExtractionService,
) -> tuple[ExtractionResult, Path, Callable[[], None]]:
    """추출을 실행하고 (결과, 임시경로, 정리콜백)을 반환한다 — `/run`·`/run.jsonl` 공유.

    스키마 없음=404, 추출 도메인 에러=422. 정리콜백은 **호출자**가 부른다(근거 렌더 등
    경로에 따라 임시 파일 사용 시점이 다르므로). 에러 경로에서는 여기서 정리한다.
    """
    schema = service.get_schema(schema_id)
    if schema is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="스키마를 찾을 수 없습니다"
        )
    path, cleanup = await _save_upload(file)
    try:
        doc_id = _content_doc_id(path)
        result = service.extract(
            path, doc_id, schema, created_by=user.user_id, prefer_vlm=vlm
        )
    except ValueError as exc:
        cleanup()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except BaseException:
        cleanup()
        raise
    return result, path, cleanup


@router.get("/results/{doc_id}", response_model=list[ExtractionResult])
def list_results(
    doc_id: str,
    _user: UserContext = Depends(get_current_user),
    service: ExtractionService = Depends(get_extraction_service),
) -> list[ExtractionResult]:
    """doc_id(문서 내용 해시)의 추출 결과 이력."""
    return service.list_results(doc_id)


# ── 업로드 헬퍼 ───────────────────────────────────────────────────────────
async def _save_upload(file: UploadFile):  # type: ignore[no-untyped-def]
    """업로드를 임시 파일로 저장하고 (경로, 정리 콜백)을 반환한다. 미지원/초과는 에러."""
    suffix = _safe_suffix(file.filename)
    if suffix not in _SUPPORTED_EXTS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "지원 형식은 PDF 또는 스캔 이미지(PNG/JPG 등)입니다"
                f"(받음: {suffix or '확장자 없음'})."
            ),
        )
    data = await file.read(_MAX_UPLOAD_BYTES + 1)
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"파일이 너무 큽니다(상한 {_MAX_UPLOAD_BYTES // (1024 * 1024)} MiB).",
        )
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)

    def cleanup() -> None:
        tmp_path.unlink(missing_ok=True)

    return tmp_path, cleanup


def _safe_suffix(name: str | None) -> str:
    """파일명에서 확장자만 보존(소문자). 경로 주입 차단."""
    if not name:
        return ""
    suffix = Path(name).suffix.lower()
    return suffix if all(c.isalnum() or c == "." for c in suffix) else ""


def _content_doc_id(path: Path) -> str:
    """파일 내용 sha256 — 멱등 doc_id(적재 doc_id와 같은 규약)."""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest
