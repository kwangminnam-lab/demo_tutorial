"""적재 라우트 (POST /v1/ingest) — 매니페스트(JSON 바디) 적재의 HTTP 경계.

진입점은 얇게: 인증·관리자 권한 확인 → 본문을 `IngestItem` 목록으로 검증(FastAPI)
→ `IngestionService.ingest_items` 호출 → 적재 리포트 반환. 추출·청킹·색인·부분
실패 처리 등 비즈니스 로직은 전부 `IngestionService`에 있다.

권한: `require_ingest_admin`으로 적재를 사장 레벨로 게이트한다(상세 RBAC는 후속).
미인증은 401, 권한 부족은 403. 메타데이터 누락·형식 오류는 FastAPI 본문 검증이
422로 막는다.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from kms.api.deps import (
    get_current_user,
    get_ingest_job_store,
    get_ingestion_service,
    require_ingest_admin,
)
from kms.config.settings import get_settings
from kms.domain.access import AccessLevel
from kms.domain.models import SourceType, UserContext
from kms.services.ingest_jobs import IngestJobStore, JobStatus
from kms.services.ingestion_service import IngestionService, IngestItem, IngestReport

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["ingest"])


class IngestJobAccepted(BaseModel):
    """업로드 접수 응답(202) — 적재는 백그라운드에서 진행, job_id로 폴링한다."""

    job_id: str
    status: JobStatus
    filename: str


class IngestJobStatusResponse(BaseModel):
    """적재 작업 상태 — done이면 report, error면 사유가 채워진다."""

    job_id: str
    filename: str
    status: JobStatus
    report: IngestReport | None = None
    error: str | None = None

# 업로드 한 파일당 상한 — 메모리/디스크 보호. diff 업로드와 동일.
_MAX_UPLOAD_BYTES = 80 * 1024 * 1024  # 80 MiB
# 허용 확장자(추출기 등록 형식). 그 외 미지원.
_ALLOWED_SUFFIXES = {".pdf", ".docx", ".pptx", ".xlsx", ".txt"}


@router.post("/ingest", response_model=list[IngestReport])
def ingest(
    items: list[IngestItem],
    _admin: UserContext = Depends(require_ingest_admin),
    service: IngestionService = Depends(get_ingestion_service),
) -> list[IngestReport]:
    """매니페스트 항목들을 적재한다. 일부 항목 실패는 부분 적재 + 실패 리포트로 보고."""
    return service.ingest_items(items)


@router.post(
    "/ingest/upload",
    response_model=IngestJobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def ingest_upload(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    source: str = Form(..., description="대상 커넥터(onedrive/googledrive/slack)"),
    user: UserContext = Depends(get_current_user),
    service: IngestionService = Depends(get_ingestion_service),
    jobs: IngestJobStore = Depends(get_ingest_job_store),
) -> IngestJobAccepted:
    """업로드한 파일을 **비동기**로 적재한다(202 접수 → 백그라운드 색인 → 폴링).

    저장 위치: `data/<source>/<filename>` (서버 측 영속). 파일을 받아 디스크에 쓴 뒤
    적재 작업을 등록하고 즉시 202(job_id)를 반환한다 — 무거운 추출+임베딩+색인은
    BackgroundTasks가 threadpool에서 처리해 요청을 막지 않는다(게이트웨이 타임아웃·
    체감 지연 완화). 진행 상태는 `GET /v1/ingest/jobs/{job_id}`로 조회한다.

    권한: 임직원 access로 색인되어 사용자 본인이 검색·열기 가능. 동일 파일명이 있으면
    덮어쓴다(콘텐츠 해시 doc_id로 멱등 — 재업로드 안전).
    """
    try:
        src = SourceType(source)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"알 수 없는 source: {source!r}",
        ) from exc
    if not file.filename:
        raise HTTPException(status_code=400, detail="filename 누락")
    # 경로 트래버설 차단: basename만 사용.
    safe_name = Path(file.filename).name
    suffix = Path(safe_name).suffix.lower()
    if suffix not in _ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"미지원 형식: {suffix or '(없음)'} (허용: {sorted(_ALLOWED_SUFFIXES)})",
        )
    data = await file.read(_MAX_UPLOAD_BYTES + 1)
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"파일이 너무 큽니다(상한 {_MAX_UPLOAD_BYTES // (1024 * 1024)} MiB).",
        )
    target_dir = Path(get_settings().data_root) / src.value
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / safe_name
    target_path.write_bytes(data)

    item = IngestItem(
        file_path=str(target_path),
        source=src,
        access=AccessLevel.임직원,
        author=user.user_id,
        author_department=user.department,
        source_url=f"local://{safe_name}",
    )
    job = jobs.create(safe_name)
    # 응답(202) 전송 후 실행 — 동기 적재를 threadpool로 offload해 이벤트 루프 비차단.
    background_tasks.add_task(_run_ingest_job, jobs, service, item, job.job_id)
    return IngestJobAccepted(job_id=job.job_id, status=job.status, filename=safe_name)


async def _run_ingest_job(
    jobs: IngestJobStore,
    service: IngestionService,
    item: IngestItem,
    job_id: str,
) -> None:
    """백그라운드 적재 1건 — 상태를 running→done/error로 갱신한다(예외는 삼키지 않고 로깅)."""
    jobs.set_running(job_id)
    try:
        report = await run_in_threadpool(service.ingest_item, item)
    except Exception:
        # 적재 실패는 전체를 죽이지 않고 작업 상태로 보고하되, 원인은 서버 로그에 남긴다
        # (조용한 실패 금지). 사용자에게는 시크릿이 섞일 수 있는 원문 대신 일반 메시지.
        logger.exception("비동기 적재 작업 실패 (job=%s)", job_id)
        jobs.set_error(job_id, "적재 처리 중 오류가 발생했습니다.")
        return
    jobs.set_done(job_id, report)


@router.get("/ingest/jobs/{job_id}", response_model=IngestJobStatusResponse)
def ingest_job_status(
    job_id: str,
    _user: UserContext = Depends(get_current_user),
    jobs: IngestJobStore = Depends(get_ingest_job_store),
) -> IngestJobStatusResponse:
    """적재 작업 상태를 조회한다 — 미존재/만료면 404."""
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="작업을 찾을 수 없습니다"
        )
    return IngestJobStatusResponse(
        job_id=job.job_id,
        filename=job.filename,
        status=job.status,
        report=job.report,
        error=job.error,
    )
