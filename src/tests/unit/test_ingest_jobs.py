"""IngestJobStore 단위 테스트 — 생성·조회·상태 전이·메모리 상한 eviction."""

from __future__ import annotations

from kms.services.ingest_jobs import IngestJobStore
from kms.services.ingestion_service import IngestReport


def test_create_registers_pending_job_with_unique_id() -> None:
    store = IngestJobStore()

    a = store.create("a.pdf")
    b = store.create("b.pdf")

    assert a.status == "pending"
    assert a.filename == "a.pdf"
    assert a.job_id != b.job_id
    assert store.get(a.job_id) is a


def test_get_unknown_returns_none() -> None:
    store = IngestJobStore()
    assert store.get("nope") is None


def test_status_transitions_running_then_done() -> None:
    store = IngestJobStore()
    job = store.create("doc.pdf")

    store.set_running(job.job_id)
    assert store.get(job.job_id).status == "running"  # type: ignore[union-attr]

    report = IngestReport(file_path="doc.pdf", ok=True, doc_id="d1", chunk_count=3)
    store.set_done(job.job_id, report)
    done = store.get(job.job_id)
    assert done is not None
    assert done.status == "done"
    assert done.report is report
    assert done.error is None


def test_set_error_records_message_not_report() -> None:
    store = IngestJobStore()
    job = store.create("doc.pdf")

    store.set_error(job.job_id, "적재 처리 중 오류가 발생했습니다.")
    failed = store.get(job.job_id)
    assert failed is not None
    assert failed.status == "error"
    assert failed.error == "적재 처리 중 오류가 발생했습니다."
    assert failed.report is None


def test_eviction_drops_oldest_beyond_max() -> None:
    store = IngestJobStore(max_jobs=2)

    first = store.create("1.pdf")
    second = store.create("2.pdf")
    third = store.create("3.pdf")  # first가 밀려난다(FIFO).

    assert store.get(first.job_id) is None
    assert store.get(second.job_id) is not None
    assert store.get(third.job_id) is not None


def test_update_on_evicted_job_is_silent_noop() -> None:
    store = IngestJobStore(max_jobs=1)
    old = store.create("old.pdf")
    store.create("new.pdf")  # old eviction.

    # 늦게 도착한 백그라운드 갱신 — 이미 폐기된 작업이라 조용히 무시(예외 없음).
    store.set_done(old.job_id, IngestReport(file_path="old.pdf", ok=True, doc_id="x"))
    assert store.get(old.job_id) is None
