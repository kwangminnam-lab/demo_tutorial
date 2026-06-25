"""비동기 적재 작업 상태 추적 — 인프로세스 in-memory job store (ADR-021).

업로드 적재는 추출+임베딩+색인이 무거워 요청을 길게 막는다(게이트웨이 타임아웃·
체감 지연·대용량 업로드 413의 한 원인). 업로드를 **202로 즉시 받고** 백그라운드
(threadpool)에서 처리한 뒤, 클라가 `job_id`로 상태를 폴링한다.

설계:
- **단일 복제(replica) 기준** — 외부 큐(Redis/Celery) 없이 동작한다(ADR 결정). 여러
  복제로 확장하면 공유 큐가 필요하다(범위 밖).
- 재시작하면 진행 중 작업 상태는 사라진다(at-most-once). 단 `doc_id`가 콘텐츠 해시라
  **재업로드가 멱등**이므로 유실된 작업은 안전하게 다시 올릴 수 있다.
- 메모리 상한(`max_jobs`)으로 오래된 완료 작업을 FIFO로 버린다(무한 누적 방지).
- 스레드 안전 — 백그라운드 스레드(threadpool)가 갱신하고 요청 스레드가 읽으므로
  `Lock`으로 보호한다.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from typing import Literal

from kms.services.ingestion_service import IngestReport

#: 작업 생애주기 — pending(접수) → running(처리 중) → done|error(종료).
JobStatus = Literal["pending", "running", "done", "error"]


@dataclass
class IngestJob:
    """적재 작업 1건의 상태 스냅샷."""

    job_id: str
    filename: str
    status: JobStatus = "pending"
    #: status=="done"일 때만 채움 — 적재 결과(doc_id·청크 수 등).
    report: IngestReport | None = None
    #: status=="error"일 때만 채움 — 사용자에게 보일 사유(시크릿 미포함).
    error: str | None = None


class IngestJobStore:
    """적재 작업 상태를 보관하는 스레드 안전 in-memory 저장소(프로세스 수명)."""

    def __init__(self, max_jobs: int = 256) -> None:
        self._jobs: dict[str, IngestJob] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()
        self._max_jobs = max_jobs

    def create(self, filename: str) -> IngestJob:
        """새 작업을 pending으로 등록하고 반환한다. 상한 초과 시 가장 오래된 작업을 버린다."""
        with self._lock:
            job = IngestJob(job_id=uuid.uuid4().hex, filename=filename)
            self._jobs[job.job_id] = job
            self._order.append(job.job_id)
            while len(self._order) > self._max_jobs:
                evicted = self._order.pop(0)
                self._jobs.pop(evicted, None)
            return job

    def get(self, job_id: str) -> IngestJob | None:
        """작업 상태를 조회한다 — 없으면 None(만료/미존재)."""
        with self._lock:
            return self._jobs.get(job_id)

    def set_running(self, job_id: str) -> None:
        self._update(job_id, status="running")

    def set_done(self, job_id: str, report: IngestReport) -> None:
        self._update(job_id, status="done", report=report)

    def set_error(self, job_id: str, message: str) -> None:
        self._update(job_id, status="error", error=message)

    def _update(
        self,
        job_id: str,
        *,
        status: JobStatus,
        report: IngestReport | None = None,
        error: str | None = None,
    ) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                # 만료(상한 eviction)된 작업의 늦은 갱신 — 조용히 무시(이미 폐기됨).
                return
            job.status = status
            if report is not None:
                job.report = report
            if error is not None:
                job.error = error
